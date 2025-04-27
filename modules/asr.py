
# asr.py
import sys, os
import time

from google.cloud import speech as gspeech

import queue
import threading
import base64

from base import RemdisModule, RemdisUpdateType

STREAMING_LIMIT = 240  # 4 minutes

def get_text_increment(module, new_text):
    iu_buffer = []

    # Split recognition result into words
    tokens = new_text.strip().split(" ")

    # Exit if there are no tokens
    if tokens == [""]:
        return iu_buffer, []

    new_tokens = []
    iu_idx = 0
    token_idx = 0
    while token_idx < len(tokens):
        # Compare past and new speech recognition results
        if iu_idx >= len(module.current_output):
            new_tokens.append(tokens[token_idx])
            token_idx += 1
        else:
            current_iu = module.current_output[iu_idx]
            iu_idx += 1
            if tokens[token_idx] == current_iu['body']:
                token_idx += 1
            else:
                # Set changed IU to REVOKE and store
                current_iu['update_type'] = RemdisUpdateType.REVOKE
                iu_buffer.append(current_iu)

    # Store new speech recognition IU in current_output
    module.current_output = [iu for iu in module.current_output if iu['update_type'] is not RemdisUpdateType.REVOKE]

    return iu_buffer, new_tokens

class ASR(RemdisModule):
    def __init__(self,
                 pub_exchanges=['asr', 'vap'],  # Added 'vap' to publish exchanges
                 sub_exchanges=['ain', 'vap']):  # Added 'vap' to subscribe for TTS events
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        self.buff_size = self.config['ASR']['buff_size']
        self.audio_buffer = queue.Queue() # Queue for receiving

        # New: Add VAP event buffer for tracking system speech
        self.vap_event_buffer = queue.Queue()

        # Speech recognition result from the previous step
        self.current_output = []

        # Variables for ASR
        self.nchunks = self.config['ASR']['chunk_size']
        self.rate = self.config['ASR']['sample_rate']

        # Silence tracking for timeout
        self.silence_start_time = time.time()  # Track when silence began
        self.is_speaking = False  # Track if user is speaking
        self.timeout_duration = self.config['TIME_OUT'].get('max_silence_time', 3.0)  # Match text_vap timeout
        self.accumulated_text = ""  # Track full utterance for timeout
        self.last_commit_time = 0  # Track last commit to prevent duplicates

        # New: Add system speaking state
        self.system_is_speaking = False
        self.system_speaking_lock = threading.Lock()

        self.client = None
        self.streaming_config = None
        self.responses = []

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.config['ASR']['json_key']

        # Variables for ASR restart
        self.asr_start_time = 0.0
        self.asr_init()

        self._is_running = True
        self.resume_asr = False

    def run(self):
        # Message receiving thread
        t1 = threading.Thread(target=self.listen_loop)
        # Speech recognition and message sending thread
        t2 = threading.Thread(target=self.produce_predictions_loop)
        # New: VAP events processing thread
        t3 = threading.Thread(target=self.process_vap_events_loop)

        # Run threads
        t1.start()
        t2.start()
        t3.start()

    def listen_loop(self):
        self.subscribe('ain', self.callback)

    def listen_vap_loop(self):
        self.subscribe('vap', self.vap_callback)

    def process_vap_events_loop(self):
        """Process VAP events to track system speech state."""
        # Start VAP subscription in separate thread
        threading.Thread(target=self.listen_vap_loop, daemon=True).start()

        while self._is_running:
            try:
                vap_event = self.vap_event_buffer.get(timeout=1.0)

                if isinstance(vap_event, dict) and 'body' in vap_event:
                    body = vap_event.get('body', {})

                    if isinstance(body, dict) and 'event' in body:
                        event_type = body.get('event')

                        if event_type == 'START_SPEECH':
                            # System is starting to speak - enable lockout
                            with self.system_speaking_lock:
                                if not self.system_is_speaking:
                                    self.system_is_speaking = True
                                    sys.stderr.write(f"ASR lockout activated: System speaking (START_SPEECH event)\n")
                                    # Send a VAP event to notify frontend of lockout
                                    self.send_asr_lockout_event(True)

                        elif event_type == 'TTS_COMMIT':
                            # System finished speaking - disable lockout with small delay to ensure
                            # all audio has been fully played before reenabling ASR
                            time.sleep(0.2)  # Small safety buffer
                            with self.system_speaking_lock:
                                if self.system_is_speaking:
                                    self.system_is_speaking = False
                                    sys.stderr.write(f"ASR lockout released: System finished speaking (TTS_COMMIT event)\n")
                                    # Send a VAP event to notify frontend of lockout release
                                    self.send_asr_lockout_event(False)

            except queue.Empty:
                # This is expected - just continue
                pass
            except Exception as e:
                sys.stderr.write(f"Error processing VAP events: {e}\n")
                time.sleep(1)  # Avoid tight loop on error

    def send_asr_lockout_event(self, is_locked):
        """Send a VAP event indicating ASR lockout state."""
        try:
            vap_event = {
                'event': 'ASR_LOCKOUT',
                'is_locked': is_locked,
                'timestamp': time.time()
            }
            vap_iu = self.createIU(vap_event, 'vap', RemdisUpdateType.ADD)
            self.publish(vap_iu, 'vap')
        except Exception as e:
            sys.stderr.write(f"Error sending ASR lockout event: {e}\n")

    def produce_predictions_loop(self):
        while self._is_running:
            # Obtain sequential speech recognition results
            requests = (
                gspeech.StreamingRecognizeRequest(audio_content=content)
                for content in self._generator()
            )

            if self.resume_asr == True:
                sys.stderr.write('Resume: ASR\n')
                self.asr_init()

            self.responses = self.client.streaming_recognize(
                self.streaming_config, requests
            )

            # Analyze speech recognition results and issue messages
            for response in self.responses:
                # Check if system is speaking - skip ASR processing if locked
                with self.system_speaking_lock:
                    if self.system_is_speaking:
                        # Skip ASR processing while system is speaking
                        continue

                # Store Google Cloud Speech-to-Text results
                p = self._extract_results(response)

                if p:
                    current_text = p['text']

                    # iu_buffer: Buffer for sending storing revoked IUs
                    # new_tokens: Token series of new speech recognition results
                    iu_buffer, new_tokens = get_text_increment(self,
                                                               current_text)

                    # Handle case when there are no tokens to issue
                    if len(new_tokens) == 0:
                        if not p['is_final']:
                            continue
                        else:
                            # Create an empty IU with a COMMIT update type when f (is_final) is True
                            output_iu = self.createIU_ASR('', [p['stability'], p['confidence']])
                            output_iu['update_type'] = RemdisUpdateType.COMMIT
                            output_iu['producer'] = 'ASR_Module'  # Add producer field for identification
                            #self.current_output = []
                            # Store in buffer for sending
                            iu_buffer.append(output_iu)

                    # When there are tokens to issue
                    for i, token in enumerate(new_tokens):
                        output_iu = self.createIU_ASR(token, [p['stability'], p['confidence']])
                        output_iu['producer'] = 'ASR_Module'  # Add producer field for identification

                        eou = p['is_final'] and i == len(new_tokens) - 1
                        if eou:
                            # Set to COMMIT at utterance end
                            output_iu['update_type'] = RemdisUpdateType.COMMIT
                        else:
                            self.current_output.append(output_iu)

                        iu_buffer.append(output_iu)

                    # Issue IUs stored in buffer for sending
                    for snd_iu in iu_buffer:
                        self.printIU(snd_iu)
                        self.publish(snd_iu, 'asr')

            # Check for timeout after processing all responses
            self.check_timeout()

    def check_timeout(self):
        """Check for silence timeout and send final commit when needed"""
        # Skip timeout check if system is speaking
        with self.system_speaking_lock:
            if self.system_is_speaking:
                return False

        current_time = time.time()

        # Only check timeout if we have accumulated text and user isn't speaking
        if self.accumulated_text and not self.is_speaking:
            silence_duration = current_time - self.silence_start_time

            if silence_duration >= self.timeout_duration:
                # Check if we've sent a commit recently to prevent duplicates
                if current_time - self.last_commit_time > 2.0:  # Only send every 2 seconds at most
                    sys.stderr.write(f"ASR silence timeout reached ({silence_duration:.2f}s). Committing text: '{self.accumulated_text}'\n")

                    # Send ASR message with accumulated text
                    commit_iu = self.createIU_ASR(self.accumulated_text, [1.0, 1.0])
                    commit_iu['update_type'] = RemdisUpdateType.COMMIT
                    commit_iu['producer'] = 'ASR_Module'
                    self.publish(commit_iu, 'asr')

                    # Also send VAP events directly
                    vap_commit_iu_body = {'event': 'ASR_COMMIT', 'text': self.accumulated_text}
                    vap_commit_iu = self.createIU(vap_commit_iu_body, 'vap', RemdisUpdateType.ADD)
                    self.publish(vap_commit_iu, 'vap')

                    vap_turn_iu_body = {'event': 'SYSTEM_TAKE_TURN'}
                    vap_turn_iu = self.createIU(vap_turn_iu_body, 'vap', RemdisUpdateType.ADD)
                    self.publish(vap_turn_iu, 'vap')

                    # Reset accumulated text after commit
                    self.accumulated_text = ""
                    self.last_commit_time = current_time
                    return True

        return False

    # Function to create IU for ASR module (store confidence scores etc.)
    def createIU_ASR(self, token, asr_result):
        iu = self.createIU(token, 'asr', RemdisUpdateType.ADD)
        iu['stability'] = asr_result[0]
        iu['confidence'] = asr_result[1]
        return iu

    # Generator that combines audio waveforms accumulated in the buffer and returns them
    def _generator(self):
        while self._is_running:
            # Restart ASR if it's about to timeout
            current_time = time.time()
            proc_time = current_time - self.asr_start_time
            if proc_time >= STREAMING_LIMIT:
                self.resume_asr = True
                break

            # Get first piece of data
            chunk = self.audio_buffer.get()
            # End process if nothing is sent
            if chunk is None:
                return
            data = [chunk]

            # Retrieve all data remaining in buffer
            while True:
                try:
                    chunk = self.audio_buffer.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            # Combine obtained data and return
            yield b"".join(data)

    def _extract_results(self, response):
        predictions = {}
        text = None
        stability = 0.0
        confidence = 0.0
        final = False

        # Analyze response from Google Cloud Speech-to-Text API
        if len(response.results) != 0:
            result = response.results[-1] # Part of interim results

            # Track speaking status for timeout detection
            was_speaking = self.is_speaking  # Track previous speaking state

            # As of 2024.1, determine is_final by the value of stability
            if result.stability < 0.8:
                conc_trans = ''
                # Combine all speech recognition results up to the current time
                for elm in response.results:
                    conc_trans += elm.alternatives[0].transcript

                # User is speaking if we have text and stability is low
                if conc_trans.strip():
                    self.is_speaking = True

                    # Update accumulated text if it's changed
                    if conc_trans != self.accumulated_text:
                        self.accumulated_text = conc_trans

                # transcript: Recognition result
                # stability: Stability of the result
                # confidence: Confidence score
                # is_final: True if utterance end
                predictions = {
                    'text': conc_trans,
                    'stability': result.stability,
                    'confidence': result.alternatives[0].confidence,
                    'is_final': result.is_final,
                }
            else:
                # User stopped speaking
                self.is_speaking = False

                # If transition from speaking to not speaking, record silence start time
                if was_speaking and not self.is_speaking:
                    self.silence_start_time = time.time()

                predictions = {
                    'text': '',
                    'stability': result.stability,
                    'confidence': result.alternatives[0].confidence,
                    'is_final': True,
                }

        return predictions

    def asr_init(self):
        self.asr_start_time = time.time()
        self.resume_asr = False

        # Construct instance of Google Cloud Speech-to-Text client
        self.client = gspeech.SpeechClient()
        config = gspeech.RecognitionConfig(
            encoding=gspeech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.rate,
            language_code=self.language,
        )
        # Settings for streaming speech recognition
        self.streaming_config = gspeech.StreamingRecognitionConfig(
            config=config, interim_results=True,
            enable_voice_activity_events=True
        )

    # Callback function for receiving messages
    def callback(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.audio_buffer.put(base64.b64decode(in_msg['body'].encode()))

    # Callback function for VAP events
    def vap_callback(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.vap_event_buffer.put(in_msg)

def main():
    asr = ASR()
    asr.run()

if __name__ == '__main__':
    main()
