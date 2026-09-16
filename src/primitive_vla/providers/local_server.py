"""Optional local Transformers VLM service; runs in a model environment."""

import argparse
import base64
from contextlib import asynccontextmanager
from io import BytesIO
import json
from pathlib import Path
import threading
from time import time


def create_app(config: dict):
    # The main Agent environment does not need torch or transformers.
    import httpx
    import torch
    from fastapi import FastAPI, HTTPException
    from PIL import Image
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

    @asynccontextmanager
    async def lifespan(app):
        path = str(Path(config['model_path']).expanduser())
        app.state.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        app.state.model = Qwen3VLForConditionalGeneration.from_pretrained(
            path, local_files_only=True, dtype=getattr(torch, config.get('dtype', 'bfloat16')),
            device_map=config.get('device', 'cuda:0'), attn_implementation=config.get('attention', 'sdpa'))
        app.state.model.eval()
        app.state.lock = threading.Lock()
        yield
        del app.state.model
        torch.cuda.empty_cache()

    app = FastAPI(title='Local Qwen VLM', lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'ready': True, 'model': config['model']}

    @app.get('/v1/models')
    def models():
        return {'object': 'list', 'data': [{'id': config['model'], 'object': 'model', 'owned_by': 'local'}]}

    def image(url):
        if url.startswith('data:image/'):
            content = base64.b64decode(url.split(',', 1)[1], validate=True)
        elif url.startswith(('http://', 'https://')):
            response = httpx.get(url, timeout=15, follow_redirects=True)
            response.raise_for_status()
            content = response.content
        else:
            raise ValueError('Expected an image data URL or HTTP(S) URL')
        with Image.open(BytesIO(content)) as source:
            return source.convert('RGB')

    @app.post('/v1/chat/completions')
    def complete(body: dict):
        if body.get('model') != config['model']:
            raise HTTPException(404, 'Unknown model')
        if body.get('stream'):
            raise HTTPException(422, 'This local service supports non-streaming responses')
        try:
            maximum = int(body.get('max_tokens', 1024))
            if not 0 < maximum <= config.get('max_output_tokens', 4096):
                raise ValueError('max_tokens exceeds the configured output budget')
            messages, images = [], []
            for message in body['messages']:
                content = message['content']
                if isinstance(content, str):
                    messages.append({'role': message['role'], 'content': content})
                    continue
                parts = []
                for part in content:
                    if part['type'] == 'text':
                        parts.append({'type': 'text', 'text': part['text']})
                    elif part['type'] == 'image_url':
                        images.append(image(part['image_url']['url']))
                        parts.append({'type': 'image', 'image': images[-1]})
                    else:
                        raise ValueError('Only text and image_url content are supported')
                messages.append({'role': message['role'], 'content': parts})
            # Serialize generation on the local GPU; FastAPI's worker pool keeps
            # health and browser requests responsive during model inference.
            with app.state.lock, torch.inference_mode():
                processor, model = app.state.processor, app.state.model
                prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                inputs = processor(text=[prompt], images=images or None, return_tensors='pt',
                                   **({'max_pixels': config.get('max_pixels', 262144)} if images else {})).to(model.device)
                temperature = float(body.get('temperature', 0))
                sampling = {'do_sample': temperature > 0}
                if temperature > 0:
                    sampling['temperature'] = temperature
                outputs = model.generate(**inputs, max_new_tokens=maximum, **sampling)
                input_tokens = inputs.input_ids.shape[1]
                tokens = outputs[0, input_tokens:]
                text = processor.decode(tokens, skip_special_tokens=True)
            return {'id': f'local-{time():.6f}', 'object': 'chat.completion', 'model': config['model'],
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text},
                                 'finish_reason': 'length' if len(tokens) == maximum else 'stop'}],
                    'usage': {'prompt_tokens': input_tokens, 'completion_tokens': len(tokens), 'total_tokens': input_tokens + len(tokens)}}
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from None

    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description='Serve local Qwen3-VL weights for Primitive VLA')
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    uvicorn.run(create_app(config), host=config.get('host', '127.0.0.1'), port=config.get('port', 8000))


if __name__ == '__main__':
    main()
