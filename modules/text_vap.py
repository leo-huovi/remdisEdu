import sys
import threading
import queue
import time
import json
import re

from base import RemdisModule, RemdisUpdateType
import prompts.util as prompt_util
from llm import ResponseChatGPT

class TextVAP(RemdisModule):
    def __init__(self,
                 pub_exchanges=['vap', 'emo_act'],
                 sub_exchanges=['asr']):
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        # Load configuration
        print(self.config)
        self.max_verbal_backchannel_num = self.config['TEXT_VAP']['max_verbal_backchannel_num']
        self.max_nonverbal_backchannel_num = self.config['TEXT_VAP']['max_nonverbal_backchannel_num']
        self.min_text_vap_threshold = self.config['TEXT_VAP']['min_text_vap_threshold']
        self.text_vap_interval = self.config['TEXT_VAP']['text_vap_interval']

        # Timeout settings (from TIME_OUT)
        self.max_silence_time = self.config['TIME_OUT']['max_silence_time']

        # Load prompts
        self.prompts = prompt_util.load_prompts(self.config['ChatGPT']['prompts'])

        # Setup buffers and state tracking
        self.input_iu_buffer = queue.Queue()
        self.output_iu_buffer = queue.Queue()
        self.llm_buffer = queue.Queue()

        # ASR status tracking
        self.last_asr_timestamp = 0
        self.accumulated_text = ""
        self.current_tokens = []
        self.last_reaction_time = 0
        self.last_call_time = 0
        self.current_emotion = "normal"
        self.current_action = "wait"
        self.is_timeout_mode = False

        # Timeout thread flag
        self.active_timeout_thread = None
        self.timeout_thread_lock = threading.Lock()

    def run(self):
        # Message receiving thread
        t1 = threading.Thread(target=self.listen_asr_loop)

        # Message processing thread
        t2 = threading.Thread(target=self.process_input_loop)

        # Execute threads
        t1.start()
        t2.start()

        # Wait for threads to complete
        t1.join()
        t2.join()

    def listen_asr_loop(self):
        self.subscribe('asr', self.callback_asr)

    def process_input_loop(self):
        while True:
            # Get input message
            input_iu = self.input_iu_buffer.get()

            # Get the update type and timestamp
            update_type = input_iu['update_type']
            current_time = input_iu['timestamp']

            # Process message based on update type
            if update_type == RemdisUpdateType.ADD:
                # Update the accumulated text
                token = input_iu['body']
                self.current_tokens.append(token)
                self.accumulated_text += token + " "
                self.last_asr_timestamp = current_time

                # Process for reactions if enough time has passed
                self.process_for_reactions()

                # If there's active input, schedule a timeout check
                self.schedule_timeout_check()

            elif update_type == RemdisUpdateType.COMMIT:
                # Final processing for the complete utterance
                self.process_commit_utterance()

                # Reset tracking variables
                self.accumulated_text = ""
                self.current_tokens = []
                self.last_reaction_time = 0
                self.last_call_time = 0
                self.is_timeout_mode = False

                # Cancel any pending timeout
                self.cancel_timeout_check()

            elif update_type == RemdisUpdateType.REVOKE:
                # Reset tracking for revoked input
                self.accumulated_text = ""
                self.current_tokens = []
                self.last_reaction_time = 0
                self.last_call_time = 0
                self.is_timeout_mode = False

                # Cancel any pending timeout
                self.cancel_timeout_check()

    def process_for_reactions(self):
        """Process the accumulated input for reactions (emotions, gestures, etc.)"""
        current_time = time.time()

        # Only process at intervals to avoid too many API calls
        if (current_time - self.last_reaction_time) >= self.text_vap_interval:
            # Only process if we have meaningful accumulated text
            if len(self.accumulated_text.strip()) >= self.min_text_vap_threshold:
                # Call LLM to get reactions
                self.get_reactions_from_llm()
                self.last_reaction_time = current_time

    def get_reactions_from_llm(self):
        """Call the LLM to get appropriate reactions based on the accumulated text"""
        current_text = self.accumulated_text.strip()
        if not current_text:
            return

        self.log(f"Call ChatGPT: query='{current_text}'")

        # Call the LLM for reactions
        llm = ResponseChatGPT(self.config, self.prompts)
        t = threading.Thread(
            target=llm.run,
            args=(time.time(),
                  current_text,
                  [],  # No dialogue history needed for reactions
                  None,
                  self.llm_buffer)
        )
        t.start()

        # Wait for LLM response
        t.join(timeout=2.0)  # Wait up to 2 seconds for response

        # Process LLM response
        if not self.llm_buffer.empty():
            try:
                llm_response = self.llm_buffer.get(timeout=0.5)
                self.process_llm_response(llm_response)
            except queue.Empty:
                self.log("No LLM response received for reactions")

    def process_llm_response(self, llm_response):
        """Process the LLM response to extract emotions and actions"""
        try:
            if hasattr(llm_response, 'response'):
                for part in llm_response.response:
                    if isinstance(part, dict):
                        # Extract emotion and action
                        if 'expression' in part and part['expression'] != 'normal':
                            new_emotion = part['expression']
                            if new_emotion != self.current_emotion:
                                self.current_emotion = new_emotion
                                self.send_emotion_action()

                        if 'action' in part and part['action'] != 'wait':
                            new_action = part['action']
                            if new_action != self.current_action:
                                self.current_action = new_action
                                self.send_emotion_action()
        except Exception as e:
            self.log(f"Error processing LLM response: {e}")

    def send_emotion_action(self):
        """Send emotion and action to the emo_act exchange"""
        emotion_action = {
            'emotion': self.current_emotion,
            'action': self.current_action
        }

        snd_iu = self.createIU(emotion_action, 'emo_act', RemdisUpdateType.ADD)
        snd_iu['data_type'] = 'emotion_action'
        self.printIU(snd_iu)
        self.publish(snd_iu, 'emo_act')

    def process_commit_utterance(self):
        """Process the complete utterance when committed"""
        # Prepare VAP event for final utterance
        vap_event = 'ASR_COMMIT'

        # Send a VAP event for the ASR complete
        snd_iu = self.createIU(vap_event, 'vap', RemdisUpdateType.ADD)
        self.printIU(snd_iu)
        self.publish(snd_iu, 'vap')

        # Reset emotion/action
        self.current_emotion = "normal"
        self.current_action = "wait"
        self.send_emotion_action()

    def schedule_timeout_check(self):
        """Schedule a timeout check to auto-commit after silence"""
        with self.timeout_thread_lock:
            # Cancel any existing timeout thread
            if self.active_timeout_thread and self.active_timeout_thread.is_alive():
                self.is_timeout_mode = False  # Signal the thread to exit

            # Create a new timeout thread
            self.is_timeout_mode = True
            self.active_timeout_thread = threading.Thread(
                target=self.timeout_monitor,
                daemon=True
            )
            self.active_timeout_thread.start()

    def cancel_timeout_check(self):
        """Cancel the timeout check"""
        with self.timeout_thread_lock:
            self.is_timeout_mode = False

    def timeout_monitor(self):
        """Monitor for silence and trigger a commit if timeout reached"""
        # Record the time we started monitoring
        start_time = time.time()
        last_text = self.accumulated_text

        while self.is_timeout_mode:
            current_time = time.time()
            elapsed = current_time - self.last_asr_timestamp

            # If input has stopped changing for the timeout period, and we have text
            if (elapsed >= self.max_silence_time and
                self.accumulated_text.strip() and
                self.accumulated_text == last_text):

                self.log(f"Timeout detected after {elapsed:.2f}s - Auto committing: '{self.accumulated_text.strip()}'")

                # Create a COMMIT message
                commit_iu = self.createIU(self.accumulated_text.strip(), 'asr', RemdisUpdateType.COMMIT)
                commit_iu['timestamp'] = current_time

                # Send it to our own input queue for processing
                self.input_iu_buffer.put(commit_iu)

                # End this timeout thread
                break

            # If the text has changed, update our reference and reset the timer
            if self.accumulated_text != last_text:
                last_text = self.accumulated_text
                self.last_asr_timestamp = current_time

            # Check every 100ms to avoid high CPU usage
            time.sleep(0.1)

    def callback_asr(self, ch, method, properties, in_msg):
        """Callback for ASR messages"""
        in_msg = self.parse_msg(in_msg)
        self.printIU(in_msg)

        # Put the message in the input queue for processing
        self.input_iu_buffer.put(in_msg)

    def log(self, *args, **kwargs):
        """Logging function with timestamp"""
        print(f"[{time.time():.5f}]", *args, flush=True, **kwargs)

def main():
    text_vap = TextVAP()
    text_vap.run()

if __name__ == '__main__':
    main()
