"""Download pinned GitHub mirrors, verify checksums, export reproducible QA CSVs."""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['id', 'dataset', 'split', 'question', 'answer', 'answer_aliases_json', 'level', 'hops', 'question_type', 'supporting_facts_json']
SEED = 'scope-public-baseline-v1-seed42'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def preserve_write(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"Refusing to replace different existing file: {path}")
        return
    path.write_bytes(data)


def select(rows, count):
    if len(rows) < count:
        raise ValueError("Not enough rows for requested sample")
    return sorted(rows, key=lambda r: sha256((SEED + ':' + r['id']).encode()))[:count]


def normalized(source, raw):
    name = source['dataset']
    text = raw.decode(source['encoding'])
    if name == 'hotpotqa':
        data = json.loads(text)
        rows = [dict(id=r['_id'], dataset=name, split='dev_distractor', question=r['question'], answer=r['answer'],
                     answer_aliases_json='[]', level=r['level'], hops='', question_type=r['type'],
                     supporting_facts_json=json.dumps(r['supporting_facts'], ensure_ascii=False)) for r in data]
    elif name == 'musique':
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
        rows = [dict(id=r['question_id'], dataset=name, split='dev_ans_v1.0_mirror', question=r['question'], answer=r['answers'][0],
                     answer_aliases_json=json.dumps(r['answers'][1:], ensure_ascii=False), level='',
                     hops=str(int(r['question_id'][0])), question_type=r['question_id'].split('__')[0],
                     supporting_facts_json=json.dumps(r.get('support_units', []), ensure_ascii=False)) for r in data]
    elif name == 'bamboogle':
        data = list(csv.DictReader(io.StringIO(text)))
        rows = [dict(id=f'bamboogle-{i:03}', dataset=name, split='public_prerelease', question=r['Question'], answer=r['Answer'],
                     answer_aliases_json=json.dumps([r['Answer 2']] if r.get('Answer 2') else [], ensure_ascii=False),
                     level='', hops='', question_type='', supporting_facts_json='[]') for i, r in enumerate(data, 1)]
    else:
        raise ValueError(name)
    if len(rows) != source['expected_rows'] or len({r['id'] for r in rows}) != len(rows):
        raise ValueError(f"Unexpected count/duplicate IDs for {name}")
    if any(not r['question'].strip() or not r['answer'].strip() for r in rows):
        raise ValueError(f"Missing question or answer for {name}")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Require existing checksum-verified raw cache')
    args = parser.parse_args()
    directory = ROOT / 'datasets'
    config = json.loads((directory / 'sources.json').read_text(encoding='utf-8'))
    raw_dir = directory / 'raw'
    raw_dir.mkdir(exist_ok=True)
    tables = {}
    for source in config['sources']:
        path = raw_dir / source['raw_filename']
        if not path.exists():
            if args.offline:
                raise FileNotFoundError(path)
            req = urllib.request.Request(source['url'], headers={'User-Agent': 'SCOPE-Dataset-Preparation/1.0'})
            with urllib.request.urlopen(req, timeout=90) as response:
                raw = response.read(source['bytes'] + 1)
            if len(raw) != source['bytes'] or sha256(raw) != source['sha256']:
                raise ValueError(f"Download size/checksum mismatch for {source['dataset']}")
            preserve_write(path, raw)
        raw = path.read_bytes()
        if len(raw) != source['bytes'] or sha256(raw) != source['sha256']:
            raise ValueError(f"Cached checksum mismatch for {source['dataset']}")
        tables[source['dataset']] = normalized(source, raw)
    hotpot, musique, bamboogle = tables['hotpotqa'], tables['musique'], tables['bamboogle']
    hop23 = [r for r in musique if r['hops'] in ('2', '3')]
    hotpot1000 = select(hotpot, 1000)
    musique1000 = select([r for r in hop23 if r['hops'] == '2'], 500) + select([r for r in hop23 if r['hops'] == '3'], 500)
    exports = {'hotpotqa_dev.csv': hotpot, 'hotpotqa_sample1000.csv': hotpot1000,
               'musique_dev.csv': musique, 'musique_2_3hop.csv': hop23,
               'musique_sample1000.csv': musique1000, 'bamboogle.csv': bamboogle,
               'scope_public_baseline2125.csv': hotpot1000 + musique1000 + bamboogle}
    outputs = []
    for name, rows in exports.items():
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        raw = buffer.getvalue().encode('utf-8-sig')  # Excel-friendly, explicit UTF-8 BOM.
        preserve_write(directory / name, raw)
        outputs.append({'file': name, 'rows': len(rows), 'sha256': sha256(raw), 'bytes': len(raw),
                        'levels': dict(Counter(r['level'] or 'unspecified' for r in rows)),
                        'hops': dict(Counter(r['hops'] or 'unspecified' for r in rows))})
    manifest = {'schema_version': 1, 'encoding': 'utf-8-sig', 'seed': SEED,
                'selection': 'sort by SHA256(seed + colon + original ID), take N; MuSiQue 500 per hop',
                'paper_exact_subset': False, 'outputs': outputs}
    preserve_write(directory / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    for result in outputs:
        print(f"{result['file']}: {result['rows']} rows, sha256={result['sha256']}")


if __name__ == '__main__':
    main()
