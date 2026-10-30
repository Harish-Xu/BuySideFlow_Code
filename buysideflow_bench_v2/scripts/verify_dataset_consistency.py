"""Read-only completion gate for canonical question/rule/manifest versions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sweagent.text2sql.semantic_comparison import source_fingerprint
from sweagent.text2sql.markdown import parse_text2sql_tasks

def check(data):
    data=Path(data)
    source=data/'question/dataset.jsonl'
    records=[json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    by_id={r['instance_id']:r for r in records}
    if len(by_id)!=len(records):raise ValueError('Duplicate canonical question IDs')
    rules=json.loads((data/'comparison_rules.json').read_text())
    errors=[]
    for qid,entry in rules.items():
        if qid not in by_id:errors.append(f'{qid}: rules refer to missing canonical question')
        elif entry['source_sha256']!=source_fingerprint(by_id[qid]):errors.append(f'{qid}: comparison-rule source fingerprint is stale')
    if errors:raise ValueError('\n'.join(errors))
    tasks=parse_text2sql_tasks(data)
    task_by_id={t['id']:t for t in tasks}
    if set(task_by_id)!=set(by_id):raise ValueError('Full loader task IDs differ from canonical question IDs')
    manifest=[json.loads(line) for line in (data/'manifest.generated.jsonl').read_text().splitlines() if line.strip()]
    m={r['id']:r for r in manifest}
    if len(m)!=len(manifest) or set(m)!=set(by_id):raise ValueError('Manifest IDs are missing, duplicated or unexpected')
    for qid,t in task_by_id.items():
        source_question=str(by_id[qid].get('instruction') or by_id[qid].get('question') or by_id[qid].get('query') or '').strip()
        # run_manifest binds the raw canonical question. The effective loader
        # appends a rendered input block, which is a different string.
        expected=hashlib.sha256(source_question.encode()).hexdigest()
        if m[qid]['question_sha256']!=expected:errors.append(f'{qid}: manifest question fingerprint is stale')
        if m[qid].get('inputs',{})!=(by_id[qid].get('inputs') or {}):errors.append(f'{qid}: manifest input parameters are stale')
        if m[qid].get('strict_output_schema',False)!=bool(by_id[qid].get('strict_output_schema',False)):
            errors.append(f'{qid}: manifest output-schema contract is stale')
        actual=t.get('comparison_rules')
        if actual:
            actual={**actual,'tables':[{k:v for k,v in table.items() if k!='raw_reference'} for table in actual['tables']]}
        if m[qid].get('comparison_rules')!=actual:errors.append(f'{qid}: manifest comparison rules differ from the effective loader rules')
    if errors:raise ValueError('\n'.join(errors))
    return {'canonical_questions':len(records),'fully_loaded_tasks':len(tasks),'manifest_records':len(manifest),
            'reviewed_rule_bindings':len(rules),'question_rule_manifest_consistent':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data/dataset')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=check(args.data)
    if args.output:args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
