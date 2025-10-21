# OpenAI Realtime API for Python
- Work in progress.  
- I'm considering migrating to the [agent SDK by OpenAI](https://github.com/openai/openai-agents-python).  

## How to use
- `interface.py` is a client-side-stateless wrapper of the Websocket interface. The only benefit is static type check.  
- `client.py` is built on top of `interface.py`. It is stateful, providing convenient client-side representations of the session.  

## Style
- Functional programming.
- Dependency injection. 
