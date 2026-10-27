"""Export saved questions and answers for manual quality review; no model calls."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Run directory or output.jsonl')
    args = parser.parse_args()
    path = args.output / 'output.jsonl' if args.output.is_dir() else args.output
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    print('# Interaction question review\n')
    print('Manual rubric: relevance to missing details; specificity; actionable information gained; '
          'redundancy; answer support in original issue/hints; subsequent agent use. '
          'Score each 0/1/2; no automatic quality score is assigned.\n')
    for row in rows:
        print('## ' + row['instance_id'] + '\n')
        print('Agent error:', row.get('error') or 'none', '\n')
        print('### Original issue (reference for answer faithfulness)\n')
        print(str(row.get('instance', {}).get('original_issue', '')) + '\n')
        turns = row.get('test_result', {}).get('interactions', [])
        print(f'User-proxy exchanges: {len(turns)} (one exchange may contain multiple questions)\n')
        for turn in turns:
            print(f"### Exchange {turn['turn']}\n")
            print('Agent:\n\n' + turn['question'] + '\n')
            print('User proxy:\n\n' + turn['answer'] + '\n')
        print('Hints (also available to proxy):\n\n' + str(row.get('instance', {}).get('hints_text', '')) + '\n')
        print('Relevant file names (also available to proxy):', row.get('instance', {}).get('files', ''), '\n')


if __name__ == '__main__':
    main()
