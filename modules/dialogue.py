import sys
import random
import threading
import queue
import time

from base import RemdisModule, RemdisState, RemdisUtil, RemdisUpdateType
from llm import ResponseChatGPT
import prompts.util as prompt_util


class Dialogue(RemdisModule):
    def __init__(self, 
                 pub_exchanges=['dialogue', 'dialogue2'],
                 sub_exchanges=['asr', 'vap', 'tts', 'bc', 'emo_act']):
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        # Load configuration
        self.history_length = self.config['DIALOGUE']['history_length']
        self.response_generation_interval = self.config['DIALOGUE']['response_generation_interval']
        self.backchannels = self.config['DIALOGUE']['backchannels']
        self.spacer = self.config['DIALOGUE']['spacer']
        self.prompts = prompt_util.load_prompts(self.config['ChatGPT']['prompts'])

        # Dialogue history
        self.dialogue_history = []

        # Buffers for IU and response processing
        self.system_utterance_end_time = 0.0
        self.input_iu_buffer = queue.Queue()
        self.bc_iu_buffer = queue.Queue()
        self.emo_act_iu_buffer = queue.Queue()
        self.output_iu_buffer = []
        self.llm_buffer = queue.Queue()

        # Manage dialogue state
        self.event_queue = queue.Queue()
        self.state = 'idle'
        self._is_running = True

        # Function for IU processing
        self.util_func = RemdisUtil()

    # Main loop
    def run(self):
        # Speech recognition result reception thread
        t1 = threading.Thread(target=self.listen_asr_loop)
        # Text-to-speech result reception thread
        t2 = threading.Thread(target=self.listen_tts_loop)
        # Turn-taking event reception thread
        t3 = threading.Thread(target=self.listen_vap_loop)
        # Backchannel generation result reception thread
        t4 = threading.Thread(target=self.listen_bc_loop)
        # Expression and behavior information reception thread
        t5 = threading.Thread(target=self.listen_emo_act_loop)
        # Continuous response generation thread
        t6 = threading.Thread(target=self.parallel_response_generation)
        # State control thread
        t7 = threading.Thread(target=self.state_management)
        # Expression and behavior control thread
        t8 = threading.Thread(target=self.emo_act_management)

        # Execute threads
        t1.start()
        t2.start()
        t3.start()
        t4.start()
        t5.start()
        t6.start()
        t7.start()
        t8.start()

    # Register callback for speech recognition result reception
    def listen_asr_loop(self):
        self.subscribe('asr', self.callback_asr)

    # Register callback for text-to-speech result reception
    def listen_tts_loop(self):
        self.subscribe('tts', self.callback_tts)

    # Register callback for VAP information reception
    def listen_vap_loop(self):
        self.subscribe('vap', self.callback_vap)

    # Register callback for backchannel reception
    def listen_bc_loop(self):
        self.subscribe('bc', self.callback_bc)

    # Register callback for expression and behavior information reception
    def listen_emo_act_loop(self):
        self.subscribe('emo_act', self.callback_emo_act)

    # Generate responses in parallel to incoming speech recognition results
    def parallel_response_generation(self):
        # Variable to hold received IUs
        iu_memory = []
        new_iu_count = 0

        while True:
            # Receive and save IU
            input_iu = self.input_iu_buffer.get()
            iu_memory.append(input_iu)
            
            # If IU is REVOKE, remove from memory
            if input_iu['update_type'] == RemdisUpdateType.REVOKE:
                iu_memory = self.util_func.remove_revoked_ius(iu_memory)
            # For ADD/COMMIT, generate response candidates
            else:
                user_utterance = self.util_func.concat_ius_body(iu_memory,
                                                                self.spacer)
                if user_utterance == '':
                    continue

                # For ADD, check if IU count exceeds threshold, otherwise wait for next IU or COMMIT
                if input_iu['update_type'] == RemdisUpdateType.ADD:
                    new_iu_count += 1
                    if new_iu_count < self.response_generation_interval:
                        continue
                    else:
                        new_iu_count = 0

                # Parallel response generation process
                # Once response starts, LLM itself is stored in the buffer
                llm = ResponseChatGPT(self.config, self.prompts)
                last_asr_iu_id = input_iu['id']
                t = threading.Thread(
                    target=llm.run,
                    args=(input_iu['timestamp'],
                          user_utterance,
                          self.dialogue_history,
                          last_asr_iu_id,
                          self.llm_buffer)
                )
                t.start()

                # Process end of user utterance
                if input_iu['update_type'] == RemdisUpdateType.COMMIT:
                    # ASR_COMMIT is only issued if user utterance occurs after the previous system utterance in time
                    if self.system_utterance_end_time < input_iu['timestamp']:
                        self.event_queue.put('ASR_COMMIT')
                    iu_memory = []

    # More stable state mangement for text_vap
    def state_management(self):
        while True:
            event = self.event_queue.get()
            prev_state = self.state

            # When ASR_COMMIT comes in, switch to listening state, but don't respond yet
            if event == 'ASR_COMMIT' and prev_state == 'idle':
                self.state = 'listening'  # Use a different state than 'talking'
                self.processing_response = False  # Not processing a response yet

            # When text_vap determines it's time to respond, now switch to talking and respond
            elif event == 'SYSTEM_TAKE_TURN' and (prev_state == 'idle' or prev_state == 'listening'):
                self.state = 'talking'
                self.processing_response = True
                self.send_response()  # Now respond to the complete sentence

            # When TTS has finished speaking, go back to idle
            elif event == 'TTS_COMMIT':
                self.state = 'idle'
                self.processing_response = False

            # Handle backchannels in idle state
            elif event == 'SYSTEM_BACKCHANNEL' and prev_state == 'idle':
                self.send_backchannel()

            self.log(f'********** State: {prev_state} -> {self.state}, Trigger: {event} **********')

    # Manage expressions and emotions
    def emo_act_management(self):
        while True:
            iu = self.emo_act_iu_buffer.get()
            # Send emotion or action
            expression_and_action = {}
            if 'emotion' in iu['body']:
                expression_and_action['expression'] = iu['body']['emotion']
            if 'action' in iu['body']:
                expression_and_action['action'] = iu['body']['action']

            if expression_and_action:
                snd_iu = self.createIU(expression_and_action, 'dialogue2', RemdisUpdateType.ADD)
                
                snd_iu['data_type'] = 'expression_and_action'
                self.printIU(snd_iu)
                self.publish(snd_iu, 'dialogue2')


    # Send system utterance
    def send_response(self):
        """
        if self.llm_buffer.empty():
            # Sleep momentarily and if still not generating a response, start system utterance
            time.sleep(0.1)
            if self.llm_buffer.empty():
                llm = ResponseChatGPT(self.config, self.prompts)
                t = threading.Thread(
                    target=llm.run,
                    args=(time.time(),
                            None,
                            self.dialogue_history,
                            None,
                            self.llm_buffer)
                )
                t.start()
        """

        # Select and send the one using the newest speech recognition result among the responses started to be generated by LLM
        selected_llm = self.llm_buffer.get()
        latest_asr_time = selected_llm.asr_time
        while not self.llm_buffer.empty():
            llm = self.llm_buffer.get()
            if llm.asr_time > latest_asr_time:
                selected_llm = llm

        # Send response
        sys.stderr.write('Resp: Selected user utterance: %s\n' % (selected_llm.user_utterance))
        if selected_llm.response is not None:
            conc_response = ''
            for part in selected_llm.response:
                # Send expression and action
                expression_and_action = {}
                if 'expression' in part and part['expression'] != 'normal':
                    expression_and_action['expression'] = part['expression']
                if 'action' in part and part['action'] != 'wait':
                    expression_and_action['action'] = part['action']
                if expression_and_action:
                    snd_iu = self.createIU(expression_and_action, 'dialogue2', RemdisUpdateType.ADD)

                    snd_iu['data_type'] = 'expression_and_action'

                    self.printIU(snd_iu)
                    self.publish(snd_iu, 'dialogue2')
                    self.output_iu_buffer.append(snd_iu)

                # Check state change during generation then send utterance
                if 'phrase' in part:
                    if self.state == 'talking':
                        snd_phrase = part['phrase']
                        # Text formatting (for demo)
                        snd_phrase = snd_phrase.replace('/', '')
                        snd_iu = self.createIU(snd_phrase, 'dialogue', RemdisUpdateType.ADD)
                        self.printIU(snd_iu)
                        self.publish(snd_iu, 'dialogue')
                        self.output_iu_buffer.append(snd_iu)
                        conc_response += snd_phrase

            # Add user utterance to dialogue context
            if selected_llm.user_utterance:
                self.history_management('user', selected_llm.user_utterance)
            else:
                self.history_management('user', '(silence)')
            self.history_management('assistant', conc_response)

        # End of response generation message
        sys.stderr.write('End of selected llm response. Waiting next user utterance.\n')
        snd_iu = self.createIU('', 'dialogue', RemdisUpdateType.COMMIT)
        self.printIU(snd_iu)
        self.publish(snd_iu, 'dialogue')

    # Send backchannel
    def send_backchannel(self):
        bc = random.choice(self.backchannels)
        snd_iu = self.createIU(bc, 'dialogue',
                               RemdisUpdateType.ADD)
        self.publish(snd_iu, 'dialogue')

        """
        iu = self.bc_iu_buffer.get()
        # Execute the following process and send backchannel only if the current state is idle
        if self.state != 'idle': 
            return
        # Send acknowledgment
        snd_iu = self.createIU(iu['body']['bc'], 'dialogue', RemdisUpdateType.ADD)
        self.printIU(snd_iu)
        self.publish(snd_iu, 'dialogue')
        """

    # Stop response
    def stop_response(self):
        for iu in self.output_iu_buffer:
            iu['update_type'] = RemdisUpdateType.REVOKE
            self.printIU(iu)
            self.publish(iu, iu['exchange'])
        self.output_iu_buffer = []

    # Callback for speech recognition result reception
    def callback_asr(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.input_iu_buffer.put(in_msg)
            
    # Callback for text-to-speech result reception
    def callback_tts(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        if in_msg['update_type'] == RemdisUpdateType.COMMIT:
            self.output_iu_buffer = []
            self.system_utterance_end_time = in_msg['timestamp']
            self.event_queue.put('TTS_COMMIT')

    # Callback for VAP information reception
    def callback_vap(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.event_queue.put(in_msg['body'])

    # Callback for backchannel reception
    def callback_bc(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.bc_iu_buffer.put(in_msg)
        self.event_queue.put('SYSTEM_BACKCHANNEL')

    # Callback for expression and behavior information reception
    def callback_emo_act(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.emo_act_iu_buffer.put(in_msg)

    # Update dialogue history
    def history_management(self, role, utt):
        self.dialogue_history.append({"role": role, "content": utt})
        if len(self.dialogue_history) > self.history_length:
            self.dialogue_history.pop(0)

    # Output log for debugging
    def log(self, *args, **kwargs):
        print(f"[{time.time():.5f}]", *args, flush=True, **kwargs)

def main():
    dialogue = Dialogue()
    dialogue.run()

if __name__ == '__main__':
    main()
