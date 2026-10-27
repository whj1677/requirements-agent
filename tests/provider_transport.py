"""HTTP envelopes matching the requested protocol; synthetic data only."""
import httpx


def reply(request, content, finish='stop', model='synthetic-mock', prompt=11, completion=7):
    if request.url.path.endswith('/responses'):
        result = dict(model=model, status='incomplete' if finish=='length' else 'completed',
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=content)])],
            usage=dict(input_tokens=prompt, output_tokens=completion, total_tokens=prompt+completion))
        if finish=='length': result['incomplete_details']={'reason':'max_output_tokens'}
    else:
        result = dict(model=model, choices=[dict(message=dict(content=content), finish_reason=finish)],
                      usage=dict(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt+completion))
    return httpx.Response(200, json=result)
