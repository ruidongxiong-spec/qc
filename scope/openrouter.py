"""Inspect the exact requested OpenRouter model; never substitute another model."""
import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import urllib.request

from .progress import stage


def read_models():
    """Public model catalog; a key is optional for discovery, required for generation."""
    key = os.environ.get('OPENROUTER_API_KEY', '')
    request = urllib.request.Request('https://openrouter.ai/api/v1/models',
                                     headers={'Authorization': 'Bearer ' + key} if key else {})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError('OpenRouter model list exceeds 8 MB')
    rows = json.loads(raw)['data']
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows):
        raise ValueError('Invalid OpenRouter model catalog')
    return rows


def free_models(rows, prefix='liquid/'):
    matches = []
    for row in rows:
        if not row['id'].startswith(prefix):
            continue
        pricing = row.get('pricing') or {}
        try:
            prices = [Decimal(str(pricing[k])) for k in ('prompt', 'completion')]
            prices.append(Decimal(str(pricing.get('request', '0'))))
        except (InvalidOperation, KeyError, TypeError):
            continue
        if all(p.is_finite() and p == 0 for p in prices):
            matches.append({k: row.get(k) for k in ('id', 'name', 'pricing', 'hugging_face_id', 'supported_parameters')})
    return sorted(matches, key=lambda r: r['id'])


def inspect_model(model):
    if not os.environ.get('OPENROUTER_API_KEY'):
        raise ValueError('Set OPENROUTER_API_KEY')
    with stage(f'OpenRouter model metadata model={model}'):
        rows = read_models()
        row = next((r for r in rows if r.get('id') == model), None)
        if row is None:
            prefix = model.split('/')[0] + '/'
            alternatives = free_models(rows, prefix)
            names = ', '.join(r['id'] for r in alternatives[:10]) or '(none)'
            raise ValueError(f'OpenRouter does not list the requested model: {model}; no model was substituted. '
                             f'Free {prefix} models in this response: {names}. '
                             'Choose an actual ID with -Model; catalog access does not validate your API key.')
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
    parser.add_argument('--model')
    parser.add_argument('--output')
    parser.add_argument('--list-models', action='store_true', help='List currently advertised free models; no generation or key required')
    parser.add_argument('--prefix', default='liquid/', help='Catalog ID prefix for --list-models')
    args = parser.parse_args()
    if args.list_models:
        matches = free_models(read_models(), args.prefix)
        print(json.dumps(matches, ensure_ascii=False, indent=2), flush=True)
        return
    if not args.model or not args.output:
        parser.error('--model and --output are required unless --list-models is used')
    metadata = inspect_model(args.model)
    Path(args.output).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"OpenRouter model={metadata['model']} advertised token prices=0 tokenizer_repo={metadata['hugging_face_id']}", flush=True)


if __name__ == '__main__':
    main()
