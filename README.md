# Chinese Haining Shadow Puppetry with AI interaction 
基于AI交互驱动的海宁皮影戏展示系统
## Requirements

Before running the system, make sure the following components are ready:

1. **Pull the Gemma 3 (4B) model via [Ollama](https://ollama.com):**

```bash
ollama pull gemma3:4b
```

2. **Download the VOSK Chinese speech recognition model:**

- Go to [VOSK Models](https://alphacephei.com/vosk/models)
- Download the model named `vosk-model-cn-0.22`
- Place the unzipped folder inside the `model-cn/` directory in this project:

```
project_root/
├── model-cn/
│   └── vosk-model-cn-0.22/  
```

## Run the system

To start the  system with LLM functionality, run:

```bash
python main.py
```

---

## How to Interact

Once the interface is running, you can interact in two modes:

### Text Mode

- Type Chinese text directly into the input box as a prompt.

###  Voice Mode

- Click the **Text Mode** button to switch to voice interaction.
- Press the **Space** key to start recording.
- Press **Space** again to stop recording.
- The recognized speech will appear in the top-left corner of the screen.

---
### Some solutions  
## **recommend environment:
Python 3.14.6
pyautogen 0.10.0
## download autogen model
pip install "autogen-agentchat"
pip install  "autogen-ext[ollama]"
pip install  "autogen-ext[openai]"

## System Start Step
1. ## run ollam

```bash
ollama serve   
ollama run gemma3:4b
```
2. ## run project:

```bash
python main.py
```

## reference
https://cloud.tencent.com/developer/article/2542898
https://blog.csdn.net/qq_36894378/article/details/159215006

