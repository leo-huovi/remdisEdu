
![remdis-logo](https://github.com/user-attachments/assets/a75b54db-9bc8-4321-a260-859bb07774f3)

# Remdis: Realtime Multimodal Dialogue System Toolkit
Remdis is a platform for developing multimodal dialogue systems. This repository contains the English version of Remdis.

## Features
- A module-based dialogue system built on asynchronous processing
- Messaging with Incremental Units (IU) and incremental processing based on Incremental Modules (IM)
- Pseudo-sequential response generation through parallel execution and streaming generation using a Large Language Model (ChatGPT)
- Turn-taking using Voice Activity Projection (VAP)
- Multimodal interaction by collaborating with MMDAgent-EX
- Python implementation, cross-platform support (Windows/MacOS/Ubuntu)
- Multimodal input/output

---------------------------------------

## Update History
- 2025/1/24: Release
---------------------------------------

## Example of a Dialogue Agent Using Remdis (Video)

[![Agent Dialogue](https://img.youtube.com/vi/mYT7nC_U3M8/0.jpg)](https://www.youtube.com/embed/mYT7nC_U3M8?si=OO5eF-8kFCdtQwIx)

*In the video, in addition to turn-taking, Audio VAP determines the timing of backchannel.*

---------------------------------------

## Installation
**Note: When installing on a Windows environment, it is recommended to use Command Prompt instead of WSL due to compatibility issues with audio devices.**
### Step 1. Preparation
Remdis uses Docker for running RabbitMQ. If you use Audio VAP with GPU support, you need to install the CUDA Toolkit and CuDNN. It can also run without a GPU, but the real-time performance may be slightly reduced.

For development and execution, we recommend using a Python environment. 
- **Install Docker Desktop**
  - MacOS
    ~~~
    brew install --cask docker
    ~~~
  - Ubuntu
    - Download and install the latest deb package ([see this page](https://docs.docker.com/desktop/install/linux/ubuntu/))
      ~~~
      sudo apt-get install ./docker-desktop-<version>-<arch>.deb
      ~~~
  - Windows
    - Download and run the installer from [Docker docs](https://docs.docker.com/desktop/install/windows-install/)
- **(Optional) Install CUDA Toolkit/CuDNN**
  - Follow the official documentation for installation on Windows/Ubuntu.
  - For installation on Windows, you will need to install Visual C++ by following these steps:
    - Download and run the [installer](https://visualstudio.microsoft.com/ja/vs/community/)
    - Check "Desktop development with C++" and proceed with the installation.

### Step 2. Install Remdis
- Clone the repository
  ~~~
  git clone https://github.com/remdis/remdis-en.git
  ~~~
- Install dependencies
  ~~~
  cd remdis

  # It is recommended to run within a virtual environment
  # Example
  conda create -n remdis python=3.11
  conda activate remdis

  # Install dependencies
  pip3 install -r requirements.txt
  ~~~

### Step 3. Obtain and Configure API Keys
- Obtain a Google Speech Cloud API key in JSON format and specify the path in config/config.yaml
  ~~~
  ASR:
   ...
   json_key: <enter your API key>
  ~~~
- Obtain an OpenAI API key and specify it in config/config.yaml
  ~~~
  ChatGPT:
    api_key: <enter your API key>
  ~~~

 ### Step 4. Install VAP
 - Clone the repository
  ~~~
  git clone https://github.com/ErikEkstedt/VAP.git
  ~~~
 - Install dependencies
  ~~~
  # Install pytorch, torchvision, and torchaudio
  # On macOS, when using the GPU, the conv1d function in the latest version of Torch is not compatible with the VAP specifications, so it is necessary to downgrade the version.
  (Optional) pip3 install torch==2.1.0 torchaudio

  # Install the main package
  pip3 install -r requirements.txt
  pip3 install -e .

  # Additionally, install torchsummary (not included in the original requirements)
  pip3 install torchsummary
  
  # Unzip the model
  # Note: This model is fine-tuned for Japanese conversation, but since the base model is trained on the Switchboard corpus, it should also work in English.
  cd models/vap
  unzip sw2japanese_public0.zip
  ~~~

### Step 5. Install MMDAgent-EX (Except for Windows)
- For OS other than Windows, follow the installation instructions on the [MMDAgent-EX official website](https://mmdagent-ex.dev/) ([How To Build](https://mmdagent-ex.dev/docs/build/)).
- For Windows, proceed to the next step as the binaries are included.

---------------------------------------

## Usage
**Note: Each IMs must be executed in a separate terminal prompt. For example, if you need to run 3 IMs, open three separate prompts, activate the virtual environment in each, and run the respective IM (Python program) in each.**

### Text Dialogue
- Run the RabbitMQ server
  ~~~
  # If using Docker Desktop, ensure the application is started beforehand
  docker run -it --rm --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3.12-management
  ~~~
- Activate the virtual environment
  ~~~
  # Example
  conda activate remdis
  ~~~
- Start 3 IMs
  ~~~
  python tin.py
  python dialogue.py
  python tout.py
  ~~~

### Spoken Dialogue
- Run the RabbitMQ server
  ~~~
  # If using Docker Desktop, ensure the application is started beforehand
  docker run -it --rm --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3.12-management
  ~~~
- Activate the virtual environment
  ~~~
  # Example
  conda activate remdis
  ~~~
- Start 6 IMs (**It is recommended to use a headset to avoid system utterances being recognized as input**)
  ~~~
  # Note: The models for speech synthesis will be downloaded by the TTS module only during the first run.
  python input.py
  python audio_vap.py or text_vap.py
  python asr.py
  python dialogue.py
  python tts.py
  python output.py
  ~~~

### Multimodal interaction with MMDAgent-EX
- Run the RabbitMQ server
  ~~~
  # If using Docker Desktop, ensure the application is started beforehand
  docker run -it --rm --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3.12-management
  ~~~
- Activate the virtual environment
  ~~~
  # Example
  conda activate remdis
  ~~~
- Start 5 IMs (**It is recommended to use a headset to avoid system utterances being recognized as input**)
  ~~~
  python input.py
  python audio_vap.py or text_vap.py
  python asr.py
  python dialogue.py
  python tts.py
  ~~~
- Start MMDAgent-EX
  - On Windows: Run `MMDAgent-EX/run.vbs`
  - On other OS: Specify the main.mdf file and run MMDAgent-EX
    ~~~
    cd MMDAgent-EX
    /somewhere/MMDAgent-EX/Release/MMDAgent-EX main.mdf
    ~~~

---------------------------------------

## TIPS
### Confirming Proper Microphone and Speaker Connection
- Run chk_mic_spk.py
  ~~~
  # If you hear your own voice played back, the setup is correct
  python input.py
  python chk_mic_spk.py
  python output.py
  ~~~

### Visualizing Audio VAP Output
- Run draw_vap_result.py
  ~~~
  # Example for spoken dialogue
  python input.py
  python audio_vap.py
  python asr.py
  python dialogue.py
  python tts.py
  python output.py
  python draw_vap_result.py
  ~~~

### Setting the System to Speak After a Certain Timeout
- Run time_out.py
  ~~~
  # Example for text dialogue
  python tin.py
  python dialogue.py
  python tout.py
  python time_out.py
  ~~~

---------------------------------------

## License
### Source Code License
The license for the original files in this repository, excluding the pre-trained VAP models under models/vap, is the Apache License 2.0. You may use it for both commercial and non-commercial purposes. 
For the license regarding the CG avatars included with MMDAgent-EX, please refer to MMDAgent-EX/asset/models/README.txt.
In addition, please be adhere to the licenses already applied to other files.

~~~
Copyright 2024 Ryuichiro Higashinaka, Koh Mitsuda, Yuya Chiba, Akinobu Lee
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
~~~

### Pre-trained VAP Model Usage Terms
The Audio VAP models are trained using the following dialogue datasets:
- [Switchboard-1 Release 2](https://catalog.ldc.upenn.edu/LDC97S62)
- [CALLHOME Japanese Speech](https://catalog.ldc.upenn.edu/LDC96S37)
- [Travel Agency Task Dialogue Corpus (Tabidachi)](https://www.nii.ac.jp/dsc/idr/rdata/Tabidachi/)
- Speech Dialogue Corpus recorded by the Higashinaka Lab at Nagoya University

The pre-trained VAP models are available for academic research purposes only. Please refer to the usage terms of each corpus. The authors disclaim any liability for claims, damages, or other responsibilities arising from the use of these pre-trained models.

### External Package Usage Terms
Remdis utilizes several external packages: [Google Cloud Speech-to-Text API](https://cloud.google.com/speech-to-text?hl=en) for speech recognition, [TTS](https://github.com/coqui-ai/TTS) for speech synthesis, [OpenAI API](https://openai.com/blog/openai-api) for dialogue generation, and [VAP](https://github.com/ErikEkstedt/VAP.git) for turn-taking. Please refer to the respective licenses of these packages.

---------------------------------------

## Citations
### Remdis
~~~
@inproceedings{remdis2024iwsds,
  title={The Remdis toolkit: Building advanced real-time multimodal dialogue systems with incremental processing and large language models},
  author={Chiba, Yuya and Mitsuda, Koh and Lee, Akinobu and Higashinaka, Ryuichiro},
  booktitle={Proc. IWSDS},
  pages={1--6},
  year={2024},
}
~~~
---------------------------------------

## Acknowledgments
This work was supported by JST Moonshot Goal 1, "Avatar-Symbiotic Society," JPMJMS2011.

