
import sys, os
import numpy as np  # Add numpy for audio level calculation
import pyaudio
import queue
import base64
import threading
import time

from base import RemdisModule, RemdisUpdateType

class AIN(RemdisModule):
    def __init__(self,
                 pub_exchanges=['ain', 'mic_level']):  # Add mic_level exchange
        super().__init__(pub_exchanges=pub_exchanges)

        self.frame_length = self.config['AIN']['frame_length']
        self.rate = self.config['AIN']['sample_rate']
        self.sample_width = self.config['AIN']['sample_width']
        self.num_audio_channel = self.config['AIN']['num_channel']
        self.chunk_size = round(self.frame_length * self.rate)

        # Declaration of the audio input stream
        self._p = pyaudio.PyAudio()
        p = self._p
        self.stream = p.open(
            format=p.get_format_from_width(self.sample_width),
            channels=self.num_audio_channel,
            rate=self.rate,
            input=True,
            output=False,
            frames_per_buffer=self.chunk_size,
            start=False,
        )
        self.stream.start_stream()

        # Volume detection settings
        self.volume_update_interval = 0.1  # Update less frequently (every 200ms) to reduce message volume
        self.last_volume_update = 0
        self.volume_smoothing = 0.4  # Increased smoothing factor for better visualization
        self.last_volume = 0

        self.is_running = True

    def run(self):
        # Message send/receive thread
        t1 = threading.Thread(target=self.listen_wav_loop)

        # Execute thread
        t1.start()

    def listen_wav_loop(self):
        while self.stream.is_active():
            # Read audio data
            input_data = self.stream.read(self.chunk_size, exception_on_overflow=False)

            # Calculate volume level
            current_time = time.time()
            if current_time - self.last_volume_update >= self.volume_update_interval:
                volume_level = self.calculate_volume(input_data)
                self.last_volume_update = current_time

                # Send volume level to web interface
                volume_iu = self.createIU({'level': volume_level}, 'mic_level', RemdisUpdateType.ADD)
                self.publish(volume_iu, 'mic_level')

            # Send raw audio data to ASR as before
            encoded_data = base64.b64encode(input_data).decode('utf-8')
            snd_iu = self.createIU(encoded_data, 'ain', RemdisUpdateType.ADD)
            self.publish(snd_iu, 'ain')

    def calculate_volume(self, audio_data):
        """Calculate the volume level from raw audio data."""
        try:
            # Convert to numpy array based on sample width
            if self.sample_width == 2:  # 16-bit audio
                fmt = np.int16
                max_val = 32768.0
            elif self.sample_width == 4:  # 32-bit audio
                fmt = np.int32
                max_val = 2147483648.0
            else:  # 8-bit audio
                fmt = np.uint8
                max_val = 128.0

            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio_data, dtype=fmt)

            # Calculate RMS (Root Mean Square)
            rms = np.sqrt(np.mean(np.square(audio_array.astype(np.float32))))

            # Normalize to 0-100 range with smoothing
            normalized_volume = min(100, (rms / max_val) * 400)  # Scale factor of 400 for better visualization
            smoothed_volume = (self.volume_smoothing * normalized_volume) + ((1 - self.volume_smoothing) * self.last_volume)
            self.last_volume = smoothed_volume

            return int(smoothed_volume)
        except Exception as e:
            sys.stderr.write(f"Error calculating volume: {e}\n")
            return 0

def main():
    ain = AIN()
    ain.run()

if __name__ == '__main__':
    main()
