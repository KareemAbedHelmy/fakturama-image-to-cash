"""Reproducible reviewer demo of the business workflow, explicitly simulator-only.

Input is reviewed candidate JSON. This is neither an image extractor nor a
Fakturama desktop adapter. Reports preserve expected failure/duplicate evidence
and the journals used to demonstrate intent-before-action and unresolved leases.
"""

import html
import json
from dataclasses import asdict
from pathlib import Path

from .domain import OrderInput, fingerprint
from .errors import ReviewRequired
from .finance import product_gross, validate_order
from .input_io import read_order, write_private
from .journal import Journal
from .simulator import Simulator, seed_simulator
from .workflow import Workflow

SCENARIOS = ('missing', 'existing', 'mixed', 'derived_unpaid', 'duplicate_debtor',
             'duplicate_product', 'uncertain_order_save', 'invalid_totals')


def _derived(source, scenario):
    data = source.model_dump(mode='json')
    if scenario == 'derived_unpaid':
        data['payment'].update(status='UNPAID', date=None)
    elif scenario == 'invalid_totals':
        data['totals']['gross'] = '0' if source.totals.gross != 0 else '0.01'
    return OrderInput.model_validate(data)


def _scenario(source, scenario, directory):
    sim = Simulator()
    candidate = _derived(source, scenario)
    if scenario in {'existing', 'mixed'}:
        seed_simulator(sim, candidate, scenario)
    elif scenario == 'duplicate_debtor':
        sim.seed_debtor(candidate.debtor)
        sim.seed_debtor(candidate.debtor)
    elif scenario == 'duplicate_product':
        item = candidate.items[0]
        sim.seed_product(item, product_gross(item))
        sim.seed_product(item, product_gross(item))
    elif scenario == 'uncertain_order_save':
        sim.fail_after = 'save_order'
    expected_error = {'duplicate_debtor': 'ambiguous_debtor',
                      'duplicate_product': 'ambiguous_product',
                      'uncertain_order_save': 'simulated_failure_after_action',
                      'invalid_totals': 'source_totals_mismatch'}.get(scenario)
    with Journal(directory / (scenario + '.sqlite')) as journal:
        flow = Workflow(sim, journal, 'simulator-demo-' + scenario)
        result = None
        error = None
        try:
            result = flow.run(candidate)
        except ReviewRequired as exc:
            error = exc.code
        if (error != expected_error) or (expected_error is None and result is None):
            raise ReviewRequired('demo_scenario_unexpected_outcome')
        retry = None
        if scenario in {'missing', 'uncertain_order_save'}:
            count = len(sim.calls)
            try:
                Workflow(sim, journal, 'simulator-demo-' + scenario).run(candidate)
            except ReviewRequired as exc:
                retry = exc.code
            if retry != 'duplicate_job' or len(sim.calls) != count:
                raise ReviewRequired('demo_duplicate_protection_failed')
        states = journal.summary()
        events = [{'stage': stage, 'status': status} for stage, status in journal.db.execute(
            'SELECT stage,status FROM events ORDER BY id')]
        leases = journal.db.execute('SELECT COUNT(*) FROM leases').fetchone()[0]
        if scenario == 'invalid_totals' and (sim.calls or states or leases):
            raise ReviewRequired('demo_invalid_input_changed_state')
        if expected_error is not None and scenario != 'invalid_totals':
            if not states or states[0]['state'] != 'REVIEW' or leases != 1:
                raise ReviewRequired('demo_unresolved_run_not_retained')
    documents = []
    for snapshot in sim.documents.values():
        documents.append({'kind': snapshot.kind, 'number': snapshot.number, 'state': snapshot.state,
                          'source_order_number': snapshot.source_order_number,
                          'net': str(snapshot.totals.net), 'vat': str(snapshot.totals.vat),
                          'gross': str(snapshot.totals.gross), 'paid': snapshot.paid,
                          'payment_method': snapshot.payment_method,
                          'payment_date': snapshot.payment_date.isoformat() if snapshot.payment_date else None,
                          'payment_value': str(snapshot.payment_value) if snapshot.payment_value is not None else None,
                          'separate_address_values_preserved': snapshot.billing == candidate.debtor.billing
                          and snapshot.delivery == candidate.debtor.delivery})
    return {'name': scenario, 'passed': True, 'expected_outcome': 'REVIEW_REQUIRED' if expected_error else 'VERIFIED',
            'observed_error': error, 'result': asdict(result) if result else None,
            'duplicate_replay_error': retry, 'calls': sim.calls,
            'order_save_count': sim.calls.count('save_order'),
            'invoice_save_count': sim.calls.count('save_invoice'),
            'journal_states': states, 'retained_lease_count': leases, 'events': events,
            'documents': documents, 'journal_file': scenario + '.sqlite',
            'input_variant': 'derived demonstration data' if scenario in {'derived_unpaid', 'invalid_totals'}
            else 'reviewed supplied candidate'}


def render_demo_html(report):
    def esc(value): return html.escape(str(value), quote=True)
    totals = report['totals']
    rows = ''.join('<tr><td>' + esc(s['name']) + '</td><td>' + esc(s['expected_outcome']) +
                   '</td><td>' + esc(s['observed_error'] or 'verified') + '</td><td>' +
                   str(s['order_save_count']) + ' / ' + str(s['invoice_save_count']) + '</td></tr>'
                   for s in report['scenarios'])
    traces = ''.join('<details><summary>' + esc(s['name']) + ' — action and journal trace</summary><pre>' +
                     esc(json.dumps({'calls': s['calls'], 'events': s['events'], 'documents': s['documents'],
                                     'duplicate_replay_error': s['duplicate_replay_error'],
                                     'retained_lease_count': s['retained_lease_count']}, indent=2)) +
                     '</pre></details>' for s in report['scenarios'])
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Fakturama reviewer demo — simulator only</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;color:#15293c}
h1{line-height:1.2}aside{padding:18px;background:#fff1cf;border-left:5px solid #c88400}table{border-collapse:collapse;width:100%;margin:24px 0}
th,td{text-align:left;padding:12px;border-bottom:1px solid #cad4df}th{background:#ecf2f8}details{margin:12px 0;border:1px solid #cad4df;padding:12px}
pre{overflow:auto;font:13px/1.5 monospace}footer{margin-top:32px;color:#526373}</style>
<h1>Fakturama Image-to-Cash<br>Reviewer demo</h1>
<aside><strong>SIMULATOR ONLY — reviewed JSON input.</strong> No image extraction or Fakturama
records are demonstrated here. “Verified” refers to the in-memory business workflow and its local
journal. The live Fakturama runs are described in docs/live-run.md.</aside>
<p><strong>Net ''' + esc(totals['net']) + ' · VAT ' + esc(totals['vat']) + ' · Gross ' + esc(totals['gross']) + ''' EUR</strong></p>
<p>The demo uses the normal workflow and journal. Failure scenarios pass when the expected
review stop occurs. The unpaid and invalid-total inputs are explicitly derived demonstrations.</p>
<table><thead><tr><th>Scenario</th><th>Expected outcome</th><th>Observed outcome</th><th>Order / Invoice saves</th></tr></thead><tbody>''' + rows + '''</tbody></table>
<h2>Inspect the execution</h2>''' + traces + '''
<footer>Simulator report only: the live Fakturama runs are described in docs/live-run.md. Each scenario uses its own journal.</footer></html>'''


def run_reviewer_demo(input_path: Path, output_dir: Path, *, reviewed_input: bool):
    if reviewed_input is not True:
        raise ReviewRequired('demo_reviewed_input_required')
    source = read_order(input_path)
    totals = validate_order(source)  # No files/scenarios before successful source validation.
    if output_dir.exists() or output_dir.is_symlink():
        raise ReviewRequired('demo_output_directory_exists')
    output_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        output_dir.mkdir(mode=0o700)
    except FileExistsError:
        raise ReviewRequired('demo_output_directory_exists') from None
    try:
        report = {'status': 'VERIFIED_SIMULATOR_DEMO', 'backend': 'SIMULATOR_ONLY',
                  'input_kind': 'reviewed_json', 'input_fingerprint': fingerprint(source),
                  'automatic_image_extraction_verified': False, 'live_workflow_verified': False,
                  'totals': totals.model_dump(mode='json'),
                  'product_gross': [{'sku': item.sku, 'gross': str(product_gross(item))}
                                    for item in source.items],
                  'scenarios': [_scenario(source, name, output_dir) for name in SCENARIOS]}
        write_private(output_dir/'report.json', json.dumps(report, ensure_ascii=False, indent=2))
        write_private(output_dir/'report.html', render_demo_html(report))
    except BaseException:
        # Keep partial journals and an exclusive marker for inspection; no cleanup/retry.
        write_private(output_dir/'incomplete.json', json.dumps({'status': 'REVIEW_REQUIRED',
                      'code': 'demo_incomplete_preserve_evidence'}))
        raise
    return report
