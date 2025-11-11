import asyncio
import math

from agents.realtime import (
    model_inputs as realtime_model_inputs, 
    config as realtime_config, 
    OpenAIRealtimeWebSocketModel, 
    RealtimeModelConfig,
    RealtimeModel, RealtimeItem, RealtimeResponse, 
    RealtimeModelEvent,
    RealtimeModelListener,
)

with open('./temp.log', 'w') as f:
    class Listener(RealtimeModelListener):
        async def on_event(self, event: RealtimeModelEvent) -> None:
            print(f'<Server> {event}', file=f)

    async def sleepVerbose(seconds: float):
        for i in range(math.ceil(seconds * 10)):
            print(f'sleeping... {i / 10 : .1f}/{seconds}', file=f)
            await asyncio.sleep(0.1)

    async def main():
        model = OpenAIRealtimeWebSocketModel()
        model.add_listener(Listener())
        try:
            await model.connect(RealtimeModelConfig())
            await sleepVerbose(1)
            await model.send_event(realtime_model_inputs.RealtimeModelSendSessionUpdate(
                session_settings = realtime_config.RealtimeSessionModelSettings(),
            ))
            await sleepVerbose(1)
            await model.send_event(realtime_model_inputs.RealtimeModelSendUserInput(
                user_input='What is three plus four?',
                start_response=False,
            ))
            await sleepVerbose(1)
            await model.send_event(realtime_model_inputs.RealtimeModelSendUserInput(
                user_input='Actually nevermind.',
                start_response=False,
            ))
            await sleepVerbose(1)
        finally:
            await model.close()

    if __name__ == "__main__":
        asyncio.run(main())
