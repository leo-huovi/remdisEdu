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
                 pub_exchanges=['asr'],
                 sub_exchanges=['ain']):
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        self.buff_size = self.config['ASR']['buff_size']
        self.audio_buffer = queue.Queue() # Queue for receiving

        # Speech recognition result from the previous step
        self.current_output = [] 

        # Variables for ASR
        self.nchunks = self.config['ASR']['chunk_size']
        self.rate = self.config['ASR']['sample_rate']

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

        # Run threads
        t1.start()
        t2.start()

    def listen_loop(self):
        self.subscribe('ain', self.callback)

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
                            #self.current_output = []
                            # Store in buffer for sending
                            iu_buffer.append(output_iu)

                    # When there are tokens to issue
                    for i, token in enumerate(new_tokens):
                        output_iu = self.createIU_ASR(token, [0.0, 0.99])
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
            # As of 2024.1, determine is_final by the value of stability
            if result.stability < 0.8:
                conc_trans = ''
                # Combine all speech recognition results up to the current time
                for elm in response.results:
                    conc_trans += elm.alternatives[0].transcript
                    
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
                predictions = predictions = {
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

def main():
    asr = ASR()
    asr.run()

if __name__ == '__main__':
    main()
