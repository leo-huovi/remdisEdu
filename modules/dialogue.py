# dialogue.py
import sys
import random
import threading
import queue
import time
import json
import yaml # Import yaml for config loading

# Assuming base.py, llm.py, prompts/util.py are accessible
try:
    from base import RemdisModule, RemdisState, RemdisUtil, RemdisUpdateType
    from llm import ResponseChatGPT
    import prompts.util as prompt_util
except ImportError as e:
    print(f"ERROR in dialogue.py: Cannot import required modules - {e}")
    sys.exit(1)

# Utility function to consume items from a generator
from collections import deque

class Dialogue(RemdisModule):
    def __init__(self,
                 config_filename='../config/config.yaml', # Make sure path is correct
                 pub_exchanges=['dialogue', 'dialogue2'],
                 sub_exchanges=['asr', 'vap', 'tts', 'bc', 'emo_act']):

        # --- Load Config FIRST ---
        try:
            with open(config_filename) as f:
                # Use safe_load for YAML files
                self._temp_config = yaml.safe_load(f)
        except FileNotFoundError:
             print(f"ERROR: Config file not found at {config_filename}")
             sys.exit(1)
        except Exception as e:
            print(f"ERROR: Failed to load/parse config {config_filename} in Dialogue init: {e}")
            sys.exit(1)

        if self._temp_config is None:
            print(f"ERROR: Config file {config_filename} loaded as None.")
            sys.exit(1)

        # Get host for super init
        rabbitmq_config = self._temp_config.get('RabbitMQ', {})
        host = rabbitmq_config.get('host', 'localhost')

        # --- Call Super Init ---
        # Pass config_filename for base class to load it again (or pass loaded dict)
        super().__init__(config_filename=config_filename, host=host,
                         pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        # --- Configuration ---
        try:
            dialogue_config = self.config.get('DIALOGUE', {})
            chatgpt_config = self.config.get('ChatGPT', {})

            self.history_length = dialogue_config.get('history_length', 5)
            self.backchannels = dialogue_config.get('backchannels', ['Uh-huh.', 'Okay.', 'I see.'])
            prompt_files = chatgpt_config.get('prompts', {})
            self.prompts = prompt_util.load_prompts(prompt_files) # Base class already loads prompts? Check base.

            # Ensure required prompts are loaded
            if 'RESP' not in self.prompts:
                 self.log("WARNING: 'RESP' prompt key not found in config. Dialogue responses may fail.")
                 # Add a fallback if needed?

        except KeyError as e:
            self.log(f"ERROR: Missing key in configuration: {e}")
            sys.exit(1)
        except Exception as e:
            self.log(f"ERROR loading DIALOGUE/ChatGPT config: {e}")
            sys.exit(1)

        # --- State and Buffers ---
        self.dialogue_history = []
        self.system_utterance_end_time = 0.0
        self.final_utterance_for_turn = None # Stores text from VAP ASR_COMMIT

        # Buffers for other inputs
        self.bc_iu_buffer = queue.Queue() # Not currently used
        self.emo_act_iu_buffer = queue.Queue() # For processing state from text_vap

        # Output tracking
        self.output_iu_buffer = [] # Tracks IUs sent for current system turn

        # LLM Interaction
        self.llm_buffer = queue.Queue() # For results from ResponseChatGPT

        # State Machine
        self.event_queue = queue.Queue()
        self.state = 'idle' # Initial state
        self.processing_response = False # Flag

        self.log("Dialogue Manager Initialized.")

    def run(self):
        self.log("Starting Dialogue Manager threads...")
        # Listener threads
        threading.Thread(target=self.listen_asr_loop, daemon=True).start()
        threading.Thread(target=self.listen_tts_loop, daemon=True).start()
        threading.Thread(target=self.listen_vap_loop, daemon=True).start()
        # threading.Thread(target=self.listen_bc_loop, daemon=True).start() # BC not used yet
        threading.Thread(target=self.listen_emo_act_loop, daemon=True).start()

        # Processing threads
        threading.Thread(target=self.state_management, daemon=True).start()
        threading.Thread(target=self.emo_act_management, daemon=True).start()

        self.log("Dialogue Manager threads running.")
        while True: time.sleep(60)


    # --- Listener Loops ---
    def listen_asr_loop(self): self.subscribe('asr', self.callback_asr)
    def listen_tts_loop(self): self.subscribe('tts', self.callback_tts)
    def listen_vap_loop(self): self.subscribe('vap', self.callback_vap)
    # def listen_bc_loop(self): self.subscribe('bc', self.callback_bc) # Not used
    def listen_emo_act_loop(self): self.subscribe('emo_act', self.callback_emo_act)

    # --- Input Callbacks ---
    def callback_asr(self, ch, method, properties, in_msg):
        """Handles messages from ASR exchange. IGNORES THEM for dialogue logic."""
        # Dialogue logic is now driven by VAP events from text_vap's timeout.
        # We don't need to process ADD/COMMIT/REVOKE from the raw ASR feed here.
        # Could add logging if needed for debug:
        # try:
        #     in_msg_data = self.parse_msg(in_msg)
        #     self.log(f"Ignoring message from 'asr' exchange: Type={in_msg_data.get('update_type')}")
        # except: pass # Ignore parsing errors too
        pass

    def callback_vap(self, ch, method, properties, in_msg):
        """Handles VAP events for state transitions and final utterance text."""
        try:
            in_msg_data = self.parse_msg(in_msg)
            vap_payload = in_msg_data.get('body', {}) # Expect dict

            if isinstance(vap_payload, dict):
                vap_event = vap_payload.get('event')
                # self.log(f"Received VAP payload: {vap_payload}") # Debug

                if vap_event == 'ASR_COMMIT':
                    final_text = vap_payload.get('text', '').strip()
                    if final_text:
                        self.log(f"Storing final utterance from VAP: '{final_text}'")
                        self.final_utterance_for_turn = final_text
                    else:
                        self.log("ASR_COMMIT VAP event received without text. Storing None.")
                        self.final_utterance_for_turn = None # Explicitly store None
                    self.event_queue.put('ASR_COMMIT') # Put the event type

                elif vap_event == 'SYSTEM_TAKE_TURN':
                    self.log(f"Putting VAP event '{vap_event}' onto event queue.")
                    self.event_queue.put('SYSTEM_TAKE_TURN')

                elif vap_event == 'TTS_COMMIT': # Allow TTS commit via VAP if needed
                    self.log(f"Putting VAP event '{vap_event}' onto event queue.")
                    self.event_queue.put('TTS_COMMIT')

                elif vap_event == 'SYSTEM_BACKCHANNEL': # Allow BC via VAP
                     self.log(f"Putting VAP event '{vap_event}' onto event queue.")
                     self.event_queue.put('SYSTEM_BACKCHANNEL')
                # Add other VAP events if needed by state machine

            # else: Handle non-dict VAP messages if necessary
            #    self.log(f"Ignoring non-dict VAP message body: {vap_payload}")

        except Exception as e:
            self.log(f"Error in callback_vap: {e}")


    def callback_tts(self, ch, method, properties, in_msg):
        """Handles TTS commit signal (alternative to VAP)."""
        try:
            in_msg_data = self.parse_msg(in_msg)
            if in_msg_data.get('update_type') == RemdisUpdateType.COMMIT:
                self.log("Received TTS COMMIT signal.")
                self.output_iu_buffer = []
                self.system_utterance_end_time = in_msg_data.get('timestamp', time.time())
                self.event_queue.put('TTS_COMMIT')
        except Exception as e:
            self.log(f"Error in callback_tts: {e}")

    # def callback_bc(self, ch, method, properties, in_msg): # Not used

    def callback_emo_act(self, ch, method, properties, in_msg):
        """Puts emotion/action messages onto their processing queue."""
        try:
            in_msg_data = self.parse_msg(in_msg)
            # No longer need to store last utterance text here
            self.emo_act_iu_buffer.put(in_msg_data)
        except Exception as e:
            self.log(f"Error in callback_emo_act: {e}")


    # --- State Machine ---
    def state_management(self):
        """Manages transitions between idle, listening, talking states."""
        self.log("Starting State Management loop...")
        while True:
            try:
                event = self.event_queue.get()
                prev_state = self.state
                self.log(f"State Machine Processing Event: '{event}', Current State: '{prev_state}'")

                if prev_state == 'idle':
                    if event == 'ASR_COMMIT': # From VAP, text is already stored
                        self.state = 'listening'
                    elif event == 'SYSTEM_TAKE_TURN': # From VAP (likely after silence)
                        self.log("SYSTEM_TAKE_TURN received while IDLE. Responding to (silence).")
                        self.state = 'talking'
                        # Ensure final_utterance is cleared if responding to silence
                        self.final_utterance_for_turn = None
                        self.trigger_response_generation(utterance="(silence)")
                    elif event == 'SYSTEM_BACKCHANNEL': # From VAP
                         self.send_backchannel()

                elif prev_state == 'listening':
                    if event == 'SYSTEM_TAKE_TURN': # From VAP (should follow ASR_COMMIT)
                        self.log("SYSTEM_TAKE_TURN received while LISTENING.")
                        utterance_to_respond = getattr(self, 'final_utterance_for_turn', None)
                        if utterance_to_respond is None:
                             self.log("ERROR: No final utterance stored. Responding to (error state).")
                             utterance_to_respond = "(error - utterance missing)"

                        self.log(f"Responding to stored utterance: '{utterance_to_respond[:50]}...'")
                        self.state = 'talking'
                        self.trigger_response_generation(utterance=utterance_to_respond)
                        self.final_utterance_for_turn = None # Clear stored utterance after use
                    elif event == 'TTS_COMMIT':
                         self.state = 'idle'
                    elif event == 'ASR_COMMIT': # Extra commit? Ignore.
                         self.log("Ignoring extra ASR_COMMIT from VAP while listening.")

                elif prev_state == 'talking':
                    if event == 'TTS_COMMIT': # System finished speaking normally
                        self.state = 'idle'
                        self.processing_response = False
                    elif event == 'ASR_COMMIT': # User barge-in (signaled by VAP commit)
                        self.log("User Barge-in detected (ASR_COMMIT from VAP while talking). Stopping response.")
                        self.stop_response()
                        self.state = 'listening'
                        self.processing_response = False
                        # Need to store the new utterance text associated with this barge-in ASR_COMMIT
                        utterance_to_respond = getattr(self, 'final_utterance_for_turn', None)
                        self.log(f"  Barge-in utterance stored: '{utterance_to_respond[:50]}...'")
                        # Don't clear self.final_utterance_for_turn here, wait for SYSTEM_TAKE_TURN

                # Log state change
                if self.state != prev_state:
                    self.log(f'********** State: {prev_state} -> {self.state}, Trigger: {event} **********')
                # else: self.log(f"Event '{event}' did not cause state change from '{prev_state}'.")

            except Exception as e:
                 self.log(f"ERROR in state_management loop: {e}")
                 import traceback; self.log(traceback.format_exc())
                 time.sleep(1)


    def trigger_response_generation(self, utterance):
        """Initiates the LLM response generation."""
        if self.processing_response:
            self.log("Ignoring trigger: Already processing response.")
            return
        if not utterance or utterance == "(error - utterance missing)":
            self.log(f"Ignoring trigger: Utterance is invalid ('{utterance}').")
            self.state = 'idle'
            return

        self.log(f"Triggering response generation for: '{utterance[:100]}...'")
        self.processing_response = True

        try:
            llm = ResponseChatGPT(self.config, self.prompts)
            threading.Thread(
                target=llm.run,
                args=(time.time(), utterance, self.dialogue_history, 'RESP', self.llm_buffer),
                daemon=True
            ).start()
            threading.Thread(target=self.send_response_from_buffer, args=(utterance,), daemon=True).start()
        except Exception as e:
             self.log(f"Error initiating LLM call: {e}")
             self.processing_response = False
             self.state = 'idle'


    def send_response_from_buffer(self, original_utterance):
         """Waits for LLM result and sends it."""
         self.log("Waiting for LLM response in buffer...")
         try:
             llm_instance = self.llm_buffer.get(block=True, timeout=10.0)
             self.log("LLM instance retrieved.")

             if self.state != 'talking':
                 self.log("State changed before sending. Aborting.")
                 self.processing_response = False
                 try: # Consume generator
                      if hasattr(llm_instance, 'response') and hasattr(llm_instance.response, '__iter__'):
                           deque(llm_instance.response, maxlen=0)
                 except Exception: pass
                 return

             full_response_text = ""
             if hasattr(llm_instance, 'response') and hasattr(llm_instance.response, '__iter__'):
                 # self.log("Streaming response...")
                 for part in llm_instance.response:
                     if self.state != 'talking':
                          self.log("State changed during streaming. Stopping.")
                          break
                     if isinstance(part, dict) and 'phrase' in part:
                         phrase = part['phrase'].strip()
                         if phrase:
                             snd_iu = self.createIU(phrase, 'dialogue', RemdisUpdateType.ADD)
                             self.publish(snd_iu, 'dialogue')
                             self.output_iu_buffer.append(snd_iu)
                             full_response_text += phrase + " " # Add space between phrases

             else: self.log("LLM response attribute is not iterable.")

             if self.state == 'talking':
                  snd_iu = self.createIU('', 'dialogue', RemdisUpdateType.COMMIT)
                  self.publish(snd_iu, 'dialogue')
                  # self.log("Sent dialogue COMMIT.")
                  if original_utterance:
                     self.history_management('user', original_utterance)
                     self.history_management('assistant', full_response_text.strip())
             # else: self.log("Did not send dialogue COMMIT (state no longer talking).")

         except queue.Empty:
              self.log("LLM response timed out. Sending default.")
              if self.state == 'talking': self.send_default_response(original_utterance)
         except Exception as e:
              self.log(f"Error sending LLM response: {e}")
              import traceback; self.log(traceback.format_exc())
              if self.state == 'talking': self.send_default_response(original_utterance)
         # finally: # Flag reset is handled by state machine transitions now
             # pass


    def send_default_response(self, original_utterance="(unknown)"):
        """Sends a canned response."""
        # ... (implementation unchanged) ...
        self.log("Sending default response.")
        default_response = "Sorry, I didn't quite catch that. Could you repeat?"
        snd_iu = self.createIU(default_response, 'dialogue', RemdisUpdateType.ADD)
        self.publish(snd_iu, 'dialogue')
        self.output_iu_buffer.append(snd_iu)
        snd_commit_iu = self.createIU('', 'dialogue', RemdisUpdateType.COMMIT)
        self.publish(snd_commit_iu, 'dialogue')
        if original_utterance: self.history_management('user', original_utterance)
        self.history_management('assistant', default_response)

    def send_backchannel(self):
        """Sends a random backchannel phrase."""
        # ... (implementation unchanged) ...
        if not self.backchannels: return
        bc = random.choice(self.backchannels)
        self.log(f"Sending backchannel: {bc}")
        snd_iu = self.createIU(bc, 'dialogue', RemdisUpdateType.ADD)
        self.publish(snd_iu, 'dialogue')


    def stop_response(self):
        """Sends REVOKE for all IUs sent in the current system turn."""
        # ... (implementation unchanged) ...
        self.log(f"Stopping current response. Revoking {len(self.output_iu_buffer)} IUs.")
        for iu in self.output_iu_buffer:
            revoke_iu = iu.copy()
            revoke_iu['update_type'] = RemdisUpdateType.REVOKE
            self.publish(revoke_iu, revoke_iu['exchange'])
        self.output_iu_buffer = []


    def emo_act_management(self):
        """Processes emotion/action updates from TextVAP."""
        # ... (implementation unchanged) ...
        while True:
            try:
                iu = self.emo_act_iu_buffer.get()
                if iu.get('data_type') == 'expression_and_action':
                    body = iu.get('body', {})
                    expression_and_action = {}
                    if 'emotion' in body: expression_and_action['expression'] = body['emotion']
                    if 'action' in body: expression_and_action['action'] = body['action']
                    if 'concept' in body: expression_and_action['concept'] = body['concept']
                    if 'current_text' in body: expression_and_action['current_text'] = body['current_text']

                    if expression_and_action:
                        snd_iu = self.createIU(expression_and_action, 'dialogue2', RemdisUpdateType.ADD)
                        snd_iu['data_type'] = 'expression_and_action'
                        self.publish(snd_iu, 'dialogue2')
            except Exception as e:
                self.log(f"Error in emo_act_management: {e}")
                time.sleep(1)


    def history_management(self, role, utt):
        """Adds utterance to history and trims."""
        # ... (implementation unchanged) ...
        if not utt: return
        self.dialogue_history.append({"role": role, "content": utt.strip()})
        if len(self.dialogue_history) > self.history_length * 2:
            self.dialogue_history = self.dialogue_history[-(self.history_length * 2):]


    def log(self, *args, **kwargs):
        """Prefixed logging"""
        log_time = time.time()
        message = ' '.join(map(str, args))
        sys.stdout.write(f"[Dialogue {log_time:.3f}] {message}\n")
        sys.stdout.flush()


# Main execution block
def main():
    config_path = '../config/config.yaml' # Adjust path if needed
    try:
         dialogue_manager = Dialogue(config_filename=config_path)
         dialogue_manager.run()
    except Exception as e: import traceback; print(f"FATAL ERROR in Dialogue main: {e}\n{traceback.format_exc()}")


if __name__ == '__main__':
    main()
