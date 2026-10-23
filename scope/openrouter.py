"""Inspect the exact requested OpenRouter model; never substitute another model."""
import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import urllib.request

from .progress import stage


def inspect_model(model):
    key = os.environ.get('OPENROUTER_API_KEY', '')
    if not key:
        raise ValueError('Set OPENROUTER_API_KEY')
    request = urllib.request.Request('https://openrouter.ai/api/v1/models',
                                     headers={'Authorization': 'Bearer ' + key})
    with stage(f'OpenRouter model metadata model={model}'):
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('OpenRouter model list exceeds 8 MB')
        rows = json.loads(raw)['data']
        row = next((r for r in rows if r.get('id') == model), None)
        if row is None:
            raise ValueError(f'OpenRouter does not list the requested model: {model}; no model was substituted')
        pricing = row.get('pricing', {})
        for field in ('prompt', 'completion'):
            try:
                price = Decimal(str(pricing[field]))
            except (InvalidOperation, KeyError):
                raise ValueError(f'Cannot verify free {field} pricing for {model}') from None
            if not price.is_finite() or price != 0:
                raise ValueError(f'{model} currently advertises nonzero/invalid {field} pricing; stopping free run')
        # Some providers also advertise a fixed request charge.
        request_price = Decimal(str(pricing.get('request', '0')))
        if not request_price.is_finite() or request_price != 0:
            raise ValueError(f'{model} advertises a nonzero/invalid request charge; stopping free run')
        repo = row.get('hugging_face_id')
        if repo is not None and (not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo)):
            raise ValueError('Invalid Hugging Face tokenizer repository in model metadata')
        return {'model': model, 'hugging_face_id': repo, 'pricing': pricing,
                'context_length': row.get('context_length'),
                'supported_parameters': row.get('supported_parameters', [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    metadata = inspect_model(args.model)
    Path(args.output).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"OpenRouter model={metadata['model']} advertised token prices=0 tokenizer_repo={metadata['hugging_face_id']}", flush=True)


if __name__ == '__main__':
    main()
