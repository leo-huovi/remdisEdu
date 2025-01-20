import sys
import json
import threading
import queue
import time
import re

import openai

from base import RemdisModule, RemdisUtil, RemdisUpdateType
from base import MMDAgentEXLabel
import prompts.util as prompt_util

class TextVAP(RemdisModule):
    def __init__(self, 
                 pub_exchanges=['bc', 'vap', 'emo_act'],
                 sub_exchanges=['asr']):
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        # ChatGPT settings
        self.client = openai.OpenAI(api_key=self.config['ChatGPT']['api_key'])
        self.model = self.config['ChatGPT']['text_vap_model']
        self.max_tokens = self.config['ChatGPT']['max_tokens']
        self.prompts = prompt_util.load_prompts(self.config['ChatGPT']['prompts'])

        # Text VAP settings
        self.min_text_vap_threshold = self.config['TEXT_VAP']['mix_text_vap_threshold']
        self.text_vap_interval = self.config['TEXT_VAP']['text_vap_interval']

        # Variables to limit the number of backchannel transmissions
        self.max_verbal_backchannel_num = self.config['TEXT_VAP']['max_verbal_backchannel_num']
        self.max_nonverbal_backchannel_num = self.config['TEXT_VAP']['max_nonverbal_backchannel_num']
        self.backchannel_sent_lock = threading.Lock()
        self.sent_verbal_backchannel_counter = 0
        self.last_verbal_backchannel_timestamp = -1
        self.sent_nonverbal_backchannel_counter = 0
        self.last_nonverbal_backchannel_timestamp = -1
        
        # Flag indicating whether user speech is being listened to or not
        self.is_listening = False

        # Buffer for IU processing
        self.input_iu_buffer = queue.Queue()
        
        # Utility function for IU processing
        self.util_func = RemdisUtil()
        self.spacer = self.config['TEXT_VAP']['spacer']

    # Main loop
    def run(self):
        t1 = threading.Thread(target=self.listen_asr_loop)
        t2 = threading.Thread(target=self.parallel_text_vap)

        t1.start()
        t2.start()

    # Register callback for receiving ASR results
    def listen_asr_loop(self):
        self.subscribe('asr', self.callback_asr)

    # Perform text VAP in parallel for incoming ASR results
    def parallel_text_vap(self):
        iu_memory = []
        new_iu_count = 0

        while True:
            # Process at the start of user speech
            if len(iu_memory) == 0:
                self.is_listening = True
                self.sent_verbal_backchannel_counter = 0
                self.sent_nonverbal_backchannel_counter = 0
                
            input_iu = self.input_iu_buffer.get()
            iu_memory.append(input_iu)
            
            # Remove from memory if IU is REVOKE
            if input_iu['update_type'] == RemdisUpdateType.REVOKE:
                iu_memory = self.util_func.remove_revoked_ius(iu_memory)
            # Generate response candidates if ADD/COMMIT
            else:
                user_utterance = self.util_func.concat_ius_body(iu_memory, spacer=self.spacer)
                if user_utterance == '':
                    continue

                # Check if enough IUs have accumulated above the threshold. If not, wait for next IU or COMMIT
                if input_iu['update_type'] == RemdisUpdateType.ADD:
                    new_iu_count += 1
                    if new_iu_count < self.text_vap_interval:
                        continue
                    else:
                        new_iu_count = 0

                # Parallel text VAP processing
                t = threading.Thread(
                    target=self.run_text_vap,
                    args=(input_iu['timestamp'],
                          user_utterance)
                )
                t.start()

                # Process at the end of user speech
                if input_iu['update_type'] == RemdisUpdateType.COMMIT:
                    self.is_listening = False
                    self.sent_backchannel_counter = 0
                    iu_memory = []
    
    # Parse text VAP determination result
    def parse_line_for_text_vap(self, line):
        text_vap_completion = line.strip().split(':')[-1]
        text_vap_score = int(text_vap_completion) if text_vap_completion.isdigit() else 0
        return text_vap_score >= self.min_text_vap_threshold

    # Parse backchannel determination result
    def parse_line_for_backchannel(self, line):
        backchannel_completion = line.strip().split(':')[-1]
        backchannel_completion = backchannel_completion.split('_')
        if len(backchannel_completion) != 2:
            return 0, ''
        label, content = backchannel_completion
        label = int(label) if label.isdigit() else 0
        return label, content
    
    # Parse emotion determination result
    def parse_line_for_expression(self, line):
        return self.parse_line_for_backchannel(line)[0]
    
    # Parse action determination result
    def parse_line_for_action(self, message):
        return self.parse_line_for_backchannel(message)[0]

    # Execute text VAP
    def run_text_vap(self, asr_timestamp, query):
        # Prompt input for ChatGPT
        messages = [
            {'role': 'user', 'content': self.prompts['BC']},
            {'role': 'system', 'content': "OK"},
            {'role': 'user', 'content': query}
        ]
        
        # Input prompt to ChatGPT and start generating response in streaming format
        self.response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            stream=True
        )

        # Variable to hold response from ChatGPT
        current_completion_line = ""
        nonverbal_backchannel = {}

        # Sequentially parse ChatGPT's response
        for chunk in self.response:
            chunk_message = chunk.choices[0].delta
            if hasattr(chunk_message, 'content'):
                new_token = chunk_message.content

                if not new_token:
                    continue

                current_completion_line += new_token
                current_completion_line = current_completion_line.strip()

                if new_token != '\n':
                    continue

                # Determine whether to give a backchannel or not
                if current_completion_line.startswith('a'):
                    label, content = self.parse_line_for_backchannel(current_completion_line)
                    if label:
                        self.log(f"***** BACKCHANNEL: {query=} {content=} *****")
                        self.send_backchannel(asr_timestamp, {'bc': content})

                # Determine emotion to be expressed
                elif current_completion_line.startswith('b'):
                    label = self.parse_line_for_expression(current_completion_line)
                    if label:
                        nonverbal_backchannel['expression'] = MMDAgentEXLabel.id2expression[label]

                # Determine action to be expressed
                elif current_completion_line.startswith('c'):
                    label = self.parse_line_for_action(current_completion_line)
                    if label:  
                        nonverbal_backchannel['action'] = MMDAgentEXLabel.id2action[label]
                    if nonverbal_backchannel:
                        self.log(f"***** BACKCHANNEL: {query=} {nonverbal_backchannel=} *****")
                        self.send_backchannel(asr_timestamp, nonverbal_backchannel)

                # Determine whether to finalize the response or not
                elif current_completion_line.startswith('d'):
                    triggered = self.parse_line_for_text_vap(current_completion_line)
                    if triggered:
                        self.log(f"***** TEXT_VAP: {query=} {current_completion_line=} *****")
                        self.send_system_take_turn()

                current_completion_line = ""

    # Send backchannel
    def send_backchannel(self, asr_timestamp, content):
        with self.backchannel_sent_lock:
            triggered = False

            if 'bc' in content:
                if (self.last_verbal_backchannel_timestamp < asr_timestamp
                    and self.is_listening
                    and self.sent_verbal_backchannel_counter < self.max_verbal_backchannel_num):
                    self.last_verbal_backchannel_timestamp = asr_timestamp
                    self.sent_verbal_backchannel_counter += 1
                    triggered = True
                    exchange = 'bc'
            elif 'expression' in content or 'action' in content:
                if (self.last_nonverbal_backchannel_timestamp < asr_timestamp
                    and self.is_listening
                    and self.sent_nonverbal_backchannel_counter < self.max_nonverbal_backchannel_num):
                    self.last_nonverbal_backchannel_timestamp = asr_timestamp
                    self.sent_nonverbal_backchannel_counter += 1
                    triggered = True
                    exchange = 'emo_act'

            if triggered:
                snd_iu = self.createIU(content, exchange, RemdisUpdateType.ADD)
                self.printIU(snd_iu)
                self.publish(snd_iu, exchange)
    
    # Send system speech start
    def send_system_take_turn(self):
        snd_iu = self.createIU('SYSTEM_TAKE_TURN', 'str', RemdisUpdateType.COMMIT)
        self.printIU(snd_iu)
        self.publish(snd_iu, 'vap')
                            
    # Callback function for message reception
    def callback_asr(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.input_iu_buffer.put(in_msg)

    # Output debug logs
    def log(self, *args, **kwargs):
        print(f"[{time.time():.5f}]", *args, flush=True, **kwargs)


def main():
    text_vap = TextVAP()
    text_vap.run()


if __name__ == '__main__':
    main()
