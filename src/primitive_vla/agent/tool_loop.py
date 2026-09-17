"""Task-independent model/tool loop; beliefs never become physical state."""
import asyncio
import time


class ToolAgent:
    def __init__(self, client, tools, prompt, log, max_calls=32, timeout_s=3600):
        self.client, self.tools, self.prompt, self.log = client, tools, prompt, log
        self.max_calls, self.timeout_s = max_calls, timeout_s

    async def run(self, instruction):
        return await asyncio.wait_for(self._run(instruction), self.timeout_s)

    async def _run(self, instruction):
        memory, history = '', []
        deadline = time.monotonic() + self.timeout_s
        for step in range(self.max_calls):
            observation = await self.tools.observe()
            context = {'instruction': instruction, 'tools': self.tools.descriptions,
                'tool_schema': self.tools.schema, 'observation': observation,
                'working_memory': memory, 'recent_results': history[-12:],
                'remaining_calls': self.max_calls-step,
                'remaining_seconds': max(0, deadline-time.monotonic())}
            self.log.write('tool_context', observation=observation, working_memory=memory,
                           remaining_calls=self.max_calls-step)
            reply = await self.client.complete('tool_plan', self.prompt, context,
                                               observation['images'])
            memory = reply['working_memory']
            call = reply['call']
            self.log.write('decision', kind='tool', call=call, reason=reply['reason'])
            if call['name'] in ('finish', 'stop'):
                report = {'status': 'completed' if call['name']=='finish' else 'stopped',
                    'agent_declared_complete': call['name']=='finish',
                    'reason': call['arguments']['evidence'], 'steps': step+1,
                    'working_memory': memory, 'results': history}
                self.log.write('task_finished', **report)
                return report
            result = await self.tools.invoke(call['name'], call['arguments'])
            history.append({'call': call, 'result': result})
            self.log.write('tool_result', call=call, result=result)
        return {'status': 'stopped', 'agent_declared_complete': False,
                'reason': 'Tool call budget exhausted', 'steps': self.max_calls,
                'working_memory': memory, 'results': history}
