# text_vap.py
import sys
import threading
import queue
import time
import json
import re
import openai # Import openai directly
import pika # Import pika for exception handling

# Assuming base.py, prompts/util.py are in the path or same directory
try:
    from base import RemdisModule, RemdisUpdateType
    import prompts.util as prompt_util
except ImportError as e:
    print(f"Error importing base/LLM modules: {e}. Make sure base.py and prompts/util.py are accessible.")
    sys.exit(1)

class TextVAP(RemdisModule):
    def __init__(self,
                 pub_exchanges=['vap', 'emo_act'],
                 sub_exchanges=['asr']):
        # --- Call Super Init FIRST ---
        super().__init__(pub_exchanges=pub_exchanges, sub_exchanges=sub_exchanges)

        self.log("Initializing TextVAP Logic...")
        try:
            # Timeout settings
            self.max_silence_time = self.config['TIME_OUT'].get('max_silence_time', 3.0)

            # Load prompts
            self.prompts = prompt_util.load_prompts(self.config['ChatGPT']['prompts'])
            self.text_vap_prompt_key = 'BC' # Key for the text_vap prompt
            if self.text_vap_prompt_key not in self.prompts:
                 self.log(f"ERROR: Prompt key '{self.text_vap_prompt_key}' not found in loaded prompts. Cannot perform reactions.")
                 self.prompts[self.text_vap_prompt_key] = "Analyze user utterance: b: 1_thinking c: 1_wait d: unknown" # Fallback

            # LLM Config
            self.api_key = self.config['ChatGPT']['api_key']
            self.vap_model = self.config['ChatGPT'].get('text_vap_model', self.config['ChatGPT']['response_generation_model'])
            self.max_tokens_vap = self.config['ChatGPT'].get('max_tokens', 100)

            # Direct OpenAI client
            self.openai_client = openai.OpenAI(api_key=self.api_key)

        except KeyError as e: self.log(f"ERROR: Missing configuration key: {e}"); sys.exit(1)
        except Exception as e: self.log(f"ERROR loading configuration/prompts: {e}"); sys.exit(1)

        # --- Buffers and State ---
        self.input_iu_buffer = queue.Queue()
        self.current_emotion = "normal"; self.current_action = "wait"; self.current_concept = ""
        self.accumulated_text = ""; self.last_asr_timestamp = 0
        self.is_timeout_mode = False; self.last_committed_text_from_input = ""

        # Timeout thread management
        self.active_timeout_thread = None; self.timeout_thread_lock = threading.Lock()

        # --- Add Publish Lock ---
        self.publish_lock = threading.Lock()
        # ---------------------------

        self.log("TextVAP Initialized.")

    # --- Publish Method Override ---
    def publish(self, message, exchange):
        """Publishes a message safely with locking and basic reconnection."""
        with self.publish_lock:
            try:
                pub_conn_details = self.pub_connections.get(exchange)
                if not pub_conn_details or \
                   not pub_conn_details['connection'] or \
                   not pub_conn_details['connection'].is_open or \
                   not pub_conn_details['channel'] or \
                   not pub_conn_details['channel'].is_open:
                    # self.log(f"Publish connection for '{exchange}' lost or invalid. Reconnecting...") # Less verbose
                    self.pub_connections[exchange] = self.mk_pub_connection(exchange)
                    pub_conn_details = self.pub_connections.get(exchange)
                    if not pub_conn_details or not pub_conn_details['channel']:
                         self.log(f"ERROR: Failed to re-establish publish connection for '{exchange}'. Message lost.")
                         return

                # --- DEBUG LOG (Temporary) ---
                # Uncomment this block to confirm publish execution for emo_act
                # if exchange == 'emo_act':
                #     self.log(f"Executing basic_publish for 'emo_act' with body: {json.dumps(message.get('body', ''))[:60]}...")
                # ---------------------------

                pub_conn_details['channel'].basic_publish(
                    exchange=exchange, routing_key='', body=json.dumps(message),
                    properties=pika.BasicProperties(content_type='application/json', delivery_mode=1) )

            except (pika.exceptions.AMQPConnectionError, pika.exceptions.ConnectionClosedByBroker,
                    pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError,
                    pika.exceptions.ChannelClosedByBroker, AssertionError, AttributeError) as e:
                self.log(f"ERROR publishing to '{exchange}': {type(e).__name__} - {e}. Attempting reconnect on next call.")
                if exchange in self.pub_connections:
                    try:
                        if self.pub_connections[exchange] and self.pub_connections[exchange]['connection'].is_open:
                             self.pub_connections[exchange]['connection'].close()
                    except Exception: pass
                    self.pub_connections[exchange] = None
            except Exception as e: self.log(f"Unexpected ERROR during publish to '{exchange}': {e}")

    def run(self):
        self.log("Starting TextVAP run loops...")
        t1 = threading.Thread(target=self.listen_asr_loop, daemon=True)
        t2 = threading.Thread(target=self.process_input_loop, daemon=True)
        t1.start(); t2.start()
        self.log("TextVAP threads started.")
        while True: time.sleep(10)

    def listen_asr_loop(self):
        self.log("Starting ASR listener...")
        try: self.subscribe('asr', self.callback_asr)
        except Exception as e: self.log(f"ERROR in listen_asr_loop: {e}")

    def process_input_loop(self):
        self.log("Starting input processing loop...")
        while True:
            try:
                input_iu = self.input_iu_buffer.get()
                update_type = input_iu.get('update_type'); current_time = input_iu.get('timestamp', time.time())

                if update_type == RemdisUpdateType.ADD:
                    new_text_segment = input_iu.get('body', ''); iu_id = input_iu.get('id', ''); is_web_partial = 'user-partial' in iu_id
                    # self.log(f"ADD Received: ID='{iu_id}', is_web_partial={is_web_partial}, Body='{new_text_segment}'") # Verbose log
                    previous_text = self.accumulated_text
                    if is_web_partial: self.accumulated_text = new_text_segment
                    elif new_text_segment: self.accumulated_text = (self.accumulated_text + " " + new_text_segment) if self.accumulated_text else new_text_segment
                    else: continue
                    self.last_asr_timestamp = current_time; text_changed = (self.accumulated_text != previous_text)
                    query_text = self.accumulated_text.strip(); should_call_llm = text_changed and bool(query_text)
                    # self.log(f"  Trigger Check: text_changed={text_changed}, query_text='{query_text}' ==> should_call_llm={should_call_llm}")
                    if should_call_llm: threading.Thread(target=self.get_reactions_directly, args=(query_text,), daemon=True).start()
                    if text_changed: self.send_emotion_action_concept(self.accumulated_text)
                    self.schedule_timeout_check()

                elif update_type == RemdisUpdateType.COMMIT:
                    committed_text = input_iu.get('body', '').strip()
                    self.log(f"Processing Frontend COMMIT signal: '{committed_text}'")
                    if not committed_text or committed_text == self.last_committed_text_from_input: self.log(f"Skipping empty or duplicate frontend COMMIT."); self.cancel_timeout_check(); continue
                    self.cancel_timeout_check(); self.log("Backend timeout cancelled due to frontend COMMIT.")
                    self.last_committed_text_from_input = committed_text; self.accumulated_text = committed_text
                    self.send_emotion_action_concept(self.accumulated_text) # Send final state
                    if committed_text: # Send VAP events
                        vap_commit_iu_body = {'event': 'ASR_COMMIT', 'text': committed_text}
                        self.publish(self.createIU(vap_commit_iu_body, 'vap', RemdisUpdateType.ADD), 'vap'); self.log(f"Published VAP event: ASR_COMMIT (from frontend commit)")
                        vap_turn_iu_body = {'event': 'SYSTEM_TAKE_TURN'}
                        self.publish(self.createIU(vap_turn_iu_body, 'vap', RemdisUpdateType.ADD), 'vap'); self.log(f"Published VAP event: SYSTEM_TAKE_TURN (from frontend commit)")
                    self.reset_state_after_commit_signal(committed_text) # Reset state

                elif update_type == RemdisUpdateType.REVOKE:
                    self.log("Processing REVOKE.")
                    self.accumulated_text = ""; self.current_emotion = "normal"; self.current_action = "wait"; self.current_concept = ""
                    self.send_emotion_action_concept(""); self.is_timeout_mode = False; self.last_committed_text_from_input = ""
                    self.cancel_timeout_check()

            except queue.Empty: time.sleep(0.1)
            except Exception as e: self.log(f"ERROR in process_input_loop: {e}"); import traceback; self.log(traceback.format_exc()); time.sleep(1)

    def reset_state_after_commit_signal(self, final_text=""):
        # self.log(f"Resetting state after commit signal. Final text context was: '{final_text}'")
        self.accumulated_text = ""; self.is_timeout_mode = False; self.current_emotion = "normal"; self.current_action = "wait"; self.current_concept = ""
        self.last_committed_text_from_input = ""; self.send_emotion_action_concept(""); self.cancel_timeout_check()

    def get_reactions_directly(self, text_to_analyze):
        """Makes a direct OpenAI API call using the BC prompt."""
        if self.text_vap_prompt_key not in self.prompts: return
        if not text_to_analyze: return
        prompt_content = self.prompts[self.text_vap_prompt_key]
        messages = [{'role': 'user', 'content': prompt_content}, {'role': 'system', 'content': 'OK. I will provide analysis in the b:/c:/d: format.'}, {'role': 'user', 'content': text_to_analyze}]
        self.log(f"Making direct API call (Model: {self.vap_model}) for text: '{text_to_analyze}'") # Log call
        try:
            response = self.openai_client.chat.completions.create(model=self.vap_model, messages=messages, max_tokens=self.max_tokens_vap, temperature=0.5, stream=False )
            if response.choices: self.parse_and_update_state(response.choices[0].message.content)
        except Exception as e: self.log(f"ERROR during direct API call: {e}")

    def parse_and_update_state(self, llm_output_string):
        """Parses the LLM string output (b:, c:, d:) and updates state."""
        if not isinstance(llm_output_string, str) or not llm_output_string: return
        # self.log(f"Parsing LLM response: '{llm_output_string}'") # Verbose log
        prev_emotion = self.current_emotion; prev_action = self.current_action; prev_concept = self.current_concept; state_changed = False
        try:
            b_match = re.search(r'b:\s*(?:\d+_)?([a-zA-Z]+)', llm_output_string, re.IGNORECASE | re.MULTILINE)
            c_match = re.search(r'c:\s*(?:\d+_)?([a-zA-Z]+)', llm_output_string, re.IGNORECASE | re.MULTILINE)
            d_match = re.search(r'd:\s*(.+?)(?:\n|a:|b:|c:|$)', llm_output_string, re.IGNORECASE | re.MULTILINE)
            if b_match: new_emotion = b_match.group(1).lower().strip();
            if new_emotion != self.current_emotion: self.current_emotion = new_emotion; state_changed = True; self.log(f"  Updated emotion: {new_emotion}")
            if c_match: new_action = c_match.group(1).lower().strip();
            if new_action != self.current_action: self.current_action = new_action; state_changed = True; self.log(f"  Updated action: {new_action}")
            if d_match: new_concept = d_match.group(1).strip().replace('"','').replace("'",'');
            if new_concept and new_concept != self.current_concept: self.current_concept = new_concept; state_changed = True; self.log(f"  Updated concept: '{new_concept}'")
            if state_changed:
                 # self.log("State updated by LLM. Sending update.") # Verbose log
                 self.send_emotion_action_concept(self.accumulated_text) # Send updated state with current buffer
        except Exception as e: self.log(f"Error parsing LLM: {e}\nString: '{llm_output_string}'")

    def send_emotion_action_concept(self, current_utterance_text):
        """Sends the current state + current text to the emo_act exchange USING THE OVERRIDDEN PUBLISH."""
        try:
            payload = { 'emotion': self.current_emotion, 'action': self.current_action,
                        'concept': self.current_concept, 'current_text': current_utterance_text.strip() }
            snd_iu = self.createIU(payload, 'emo_act', RemdisUpdateType.ADD)
            snd_iu['data_type'] = 'expression_and_action'

            # --- DEBUG LOG: Confirm publish attempt ---
            self.log(f"Attempting to publish state update to 'emo_act': {payload}")
            # ------------------------------------------
            self.publish(snd_iu, 'emo_act')

        except Exception as e: self.log(f"ERROR preparing payload for send_emotion_action_concept: {e}")

    # --- Timeout Logic ---
    def schedule_timeout_check(self):
        with self.timeout_thread_lock: self.is_timeout_mode = False; self.active_timeout_thread = threading.Thread(target=self.timeout_monitor, daemon=True); self.is_timeout_mode = True; self.active_timeout_thread.start()

    def cancel_timeout_check(self):
        with self.timeout_thread_lock:
             if self.is_timeout_mode: self.is_timeout_mode = False

    def timeout_monitor(self):
        start_check_time = self.last_asr_timestamp
        while self.is_timeout_mode:
            current_time = time.time(); time_since_last_input = current_time - self.last_asr_timestamp
            if time_since_last_input >= self.max_silence_time:
                if not self.is_timeout_mode: break
                final_text = self.accumulated_text.strip()
                self.log(f"Backend Timeout detected ({time_since_last_input:.2f}s). Final Text: '{final_text}'")
                if final_text:
                    vap_commit_iu_body = {'event': 'ASR_COMMIT', 'text': final_text}; self.publish(self.createIU(vap_commit_iu_body, 'vap', RemdisUpdateType.ADD), 'vap'); self.log(f"Published VAP event: ASR_COMMIT (from backend timeout)")
                    vap_turn_iu_body = {'event': 'SYSTEM_TAKE_TURN'}; self.publish(self.createIU(vap_turn_iu_body, 'vap', RemdisUpdateType.ADD), 'vap'); self.log(f"Published VAP event: SYSTEM_TAKE_TURN (from backend timeout)")
                else: self.log("Backend Timeout detected, but no text accumulated. No VAP events sent.")
                self.reset_state_after_commit_signal(final_text)
                self.is_timeout_mode = False; break
            time.sleep(0.2)

    # --- Standard Callbacks and Logging ---
    def callback_asr(self, ch, method, properties, in_msg):
        try: self.input_iu_buffer.put(self.parse_msg(in_msg))
        except Exception as e: self.log(f"ERROR in callback_asr: {e}")

    def log(self, *args, **kwargs):
        log_time = time.time(); message = ' '.join(map(str, args)); sys.stdout.write(f"[TextVAP {log_time:.3f}] {message}\n"); sys.stdout.flush()

# --- Main Execution ---
def main():
    try: text_vap = TextVAP(); text_vap.run()
    except Exception as e: import traceback; print(f"FATAL: {e}\n{traceback.format_exc()}")
if __name__ == '__main__': main()
