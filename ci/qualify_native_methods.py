#!/usr/bin/env python3
"""Positive native RPC qualification in a credential-free disposable home."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from qualify_codex_schema import _read_jsonrpc_response, _write_jsonrpc

ROOT = Path(__file__).resolve().parents[1]
METHODS = (
    'app/read', 'server/diagnostics', 'thread/backgroundTerminals/list',
    'thread/backgroundTerminals/terminate', 'thread/queue/add',
    'thread/queue/delete', 'thread/queue/list', 'thread/queue/reorder',
    'thread/queue/start', 'thread/queue/update', 'thread/search',
    'thread/searchOccurrences', 'thread/settings/update',
    'thread/timeline/list', 'turn/settings/update',
)


def validate(schema, value, root=None):
    """Validate the structural fields consumed by CAS, including local refs."""
    root = schema if root is None else root
    if '$ref' in schema:
        target = root
        for part in schema['$ref'].removeprefix('#/').split('/'):
            target = target[part.replace('~1', '/').replace('~0', '~')]
        validate(target, value, root)
    for choice in ('oneOf', 'anyOf'):
        if choice in schema:
            count = 0
            for option in schema[choice]:
                try:
                    validate(option, value, root)
                    count += 1
                except (ValueError, KeyError):
                    pass
            if (choice == 'oneOf' and count != 1) or (choice == 'anyOf' and count == 0):
                raise ValueError('response union shape mismatch')
    kinds = schema.get('type')
    if kinds:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        matches = {
            'null': value is None, 'object': isinstance(value, dict),
            'array': isinstance(value, list), 'string': isinstance(value, str),
            'boolean': type(value) is bool, 'integer': type(value) is int,
            'number': type(value) in (int, float),
        }
        if not any(matches.get(kind, False) for kind in kinds):
            raise ValueError('response field type mismatch')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('response enum mismatch')
    if isinstance(value, dict):
        if not set(schema.get('required', ())) <= set(value):
            raise ValueError('response required fields missing')
        for key, child in value.items():
            field = schema.get('properties', {}).get(key)
            if field is not None:
                validate(field, child, root)
    if isinstance(value, list) and isinstance(schema.get('items'), dict):
        for child in value:
            validate(schema['items'], child, root)


def qualify(binary, schemas, temporary_root, *, local_provider=False, terminal_fixture=False):
    contracts = json.loads((ROOT / 'tests/fixtures/protocol/codex_0.160.1_operator_rpc_contracts.json').read_text())['contracts']
    schema_paths = {path.stem: path for path in schemas.rglob('*.json')}
    evidence = {'native_version': '0.166.0', 'binary_sha256': hashlib.file_digest(binary.open('rb'), 'sha256').hexdigest(),
                'methods': {}, 'blocked_methods': {}}
    with tempfile.TemporaryDirectory(prefix='native-method-smoke-', dir=temporary_root) as scratch:
        home = Path(scratch)
        codex_home = home / 'codex'
        codex_home.mkdir()
        provider_release = threading.Event()
        provider_called = threading.Event()
        provider_requests = []
        provider = None
        if local_provider:
            class HoldingProvider(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    provider_requests.append(True)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    response = {'id': 'qualification-' + uuid.uuid4().hex, 'object': 'response',
                                'created_at': int(time.time()), 'status': 'in_progress', 'output': []}

                    def send(kind, **fields):
                        event = {'type': kind, **fields}
                        self.wfile.write(('event: ' + kind + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                        self.wfile.flush()

                    send('response.created', response=response)
                    provider_called.set()
                    if terminal_fixture and len(provider_requests) == 1:
                        arguments = json.dumps({'cmd': 'sleep 8', 'yield_time_ms': 1000, 'max_output_tokens': 100})
                        item = {'id': 'qualification-call', 'type': 'function_call', 'call_id': 'qualification-call',
                                'name': 'exec_command', 'arguments': arguments}
                        send('response.output_item.added', output_index=0, item={**item, 'arguments': ''})
                        send('response.function_call_arguments.delta', item_id=item['id'], output_index=0, delta=arguments)
                        send('response.function_call_arguments.done', item_id=item['id'], output_index=0, arguments=arguments)
                        send('response.output_item.done', output_index=0, item=item)
                        send('response.completed', response={**response, 'status': 'completed', 'output': [item]})
                    else:
                        provider_release.wait(20)

            provider = ThreadingHTTPServer(('127.0.0.1', 0), HoldingProvider)
            threading.Thread(target=provider.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
            (codex_home / 'config.toml').write_text(
                'model = "gpt-5.5"\nmodel_provider = "cas-qualification"\n'
                '[features]\nstep_model_switching = true\n'
                '[model_providers.cas-qualification]\nname = "Local qualification"\n'
                f'base_url = "http://127.0.0.1:{provider.server_port}"\nwire_api = "responses"\n'
                'requires_openai_auth = false\nsupports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'CODEX_HOME': str(codex_home),
               'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_CONFIG_HOME': str(home / 'config'),
               'XDG_DATA_HOME': str(home / 'data')}
        process = subprocess.Popen([str(binary), 'app-server', '--listen', 'stdio://'],
                                   cwd=home, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        buffer = bytearray()
        sequence = 0

        def rpc(method, params):
            nonlocal sequence
            sequence += 1
            _write_jsonrpc(process, {'id': sequence, 'method': method, 'params': params})
            reply = _read_jsonrpc_response(process, buffer, sequence)
            if 'error' in reply or not isinstance(reply.get('result'), dict):
                code = reply.get('error', {}).get('code', 'INVALID_RESULT')
                message = reply.get('error', {}).get('message', '')
                raise ValueError('native_rpc_error_' + str(code) + ': ' + message[:180])
            return reply['result']

        def probe(method, params, *, expected_queue_ids=None):
            try:
                contract = contracts[method]
                request = json.loads(schema_paths[contract['native_request_type']].read_text())
                response = json.loads(schema_paths[contract['native_response_type']].read_text())
                validate(request, params)
                result = rpc(method, params)
                validate(response, result)
                effects = {}
                if method in ('thread/queue/add', 'thread/queue/update'):
                    item = result.get('queuedSubmission', {})
                    if not isinstance(item.get('id'), str) or not item['id']:
                        raise ValueError('queue response has no item identity')
                    effects['item_id_present'] = True
                    if method.endswith('/update'):
                        if item['id'] != params['queuedSubmissionId'] or item.get('input') != params['input']:
                            raise ValueError('queue update did not return the changed item')
                        effects['updated_input_matches'] = True
                if method == 'thread/queue/delete':
                    if result.get('deleted') is not True:
                        raise ValueError('queue deletion did not delete an item')
                    effects['deleted'] = True
                if method == 'thread/queue/list' and expected_queue_ids is not None:
                    ids = [entry.get('id') for entry in result['data']]
                    if ids != expected_queue_ids:
                        raise ValueError('queue list did not contain expected item order')
                    effects['expected_items_and_order_observed'] = True
                if method == 'thread/queue/start':
                    if not isinstance(result.get('turn', {}).get('id'), str):
                        raise ValueError('queue start did not create an identified turn')
                    effects['turn_id_present'] = True
                if method == 'turn/settings/update':
                    if result.get('status') != 'applied':
                        raise ValueError('active turn settings were not applied')
                    effects['settings_applied'] = True
                if method == 'thread/backgroundTerminals/terminate':
                    if result.get('terminated') is not True:
                        raise ValueError('native background terminal was not terminated')
                    effects['terminated'] = True
                evidence['methods'][method] = {'request_schema': request, 'response_schema': response,
                    'protocol_smoke': {'status': 'passed', 'positive': True,
                                       'fixture_mode': 'closed-loopback-provider' if local_provider else 'default-native',
                                       'response_fields': sorted(result), 'effects': effects}}
                if method == 'turn/settings/update':
                    evidence['methods'][method]['protocol_smoke']['required_features'] = {'step_model_switching': True}
                return result
            except (OSError, KeyError, ValueError, RuntimeError, TimeoutError) as error:
                evidence['blocked_methods'][method] = {'status': 'blocked', 'reason': str(error) if isinstance(error, ValueError) else type(error).__name__}
                return None

        try:
            rpc('initialize', {'clientInfo': {'name': 'cas-method-qualification', 'version': '1'},
                               'capabilities': {'experimentalApi': True}})
            _write_jsonrpc(process, {'method': 'initialized', 'params': {}})
            started = rpc('thread/start', {'cwd': str(home), 'approvalPolicy': 'never', 'sandbox': 'read-only'})
            thread_id = started['thread']['id']
            rpc('thread/name/set', {'threadId': thread_id, 'name': 'cas qualification fixture'})
            probe('server/diagnostics', {})
            probe('app/read', {'appIds': [], 'includeTools': False})
            probe('thread/search', {'searchTerm': 'cas qualification fixture', 'limit': 2})
            probe('thread/searchOccurrences', {'threadId': thread_id, 'searchTerm': 'cas qualification fixture', 'limit': 2})
            probe('thread/timeline/list', {'threadId': thread_id, 'limit': 2})
            probe('thread/settings/update', {'threadId': thread_id, 'effort': 'low'})
            probe('thread/backgroundTerminals/list', {'threadId': thread_id, 'limit': 2})
            probe('thread/queue/list', {'threadId': thread_id, 'limit': 2})
            message = [{'type': 'text', 'text': 'cas qualification fixture message', 'text_elements': []}]
            active = rpc('turn/start', {'threadId': thread_id, 'input': message}) if local_provider else None
            if active and not provider_called.wait(3):
                raise ValueError('Local qualification provider was not reached')
            if active and not terminal_fixture:
                probe('turn/settings/update', {'threadId': thread_id, 'turnId': active['turn']['id'], 'effort': 'low'})
            if terminal_fixture:
                terminal = None
                for _attempt in range(10):
                    listed = probe('thread/backgroundTerminals/list', {'threadId': thread_id, 'limit': 10})
                    if listed and listed['data']:
                        terminal = listed['data'][0]
                        break
                    time.sleep(0.5)
                if terminal and terminal.get('cwd') == str(home):
                    terminated = probe('thread/backgroundTerminals/terminate', {'threadId': thread_id, 'processId': terminal['processId']})
                    listed = probe('thread/backgroundTerminals/list', {'threadId': thread_id, 'limit': 10})
                    if terminated and listed is not None and all(item['processId'] != terminal['processId'] for item in listed['data']):
                        evidence['methods']['thread/backgroundTerminals/terminate']['protocol_smoke']['effects']['process_removed_observed'] = True
                    else:
                        evidence['methods'].pop('thread/backgroundTerminals/terminate', None)
                rpc('turn/interrupt', {'threadId': thread_id, 'turnId': active['turn']['id']})
            first = probe('thread/queue/add', {'threadId': thread_id, 'input': message, 'clientUserMessageId': str(uuid.uuid4())}) if local_provider and not terminal_fixture else None
            second = probe('thread/queue/add', {'threadId': thread_id, 'input': message, 'clientUserMessageId': str(uuid.uuid4())}) if local_provider and not terminal_fixture else None
            if first and second:
                first_id = first['queuedSubmission']['id']
                second_id = second['queuedSubmission']['id']
                updated = [{'type': 'text', 'text': 'cas qualification fixture updated', 'text_elements': []}]
                probe('thread/queue/list', {'threadId': thread_id, 'limit': 10}, expected_queue_ids=[first_id, second_id])
                probe('thread/queue/update', {'threadId': thread_id, 'queuedSubmissionId': first_id, 'input': updated})
                if second_id:
                    probe('thread/queue/reorder', {'threadId': thread_id, 'queuedSubmissionIds': [second_id, first_id]})
                    ordered = probe('thread/queue/list', {'threadId': thread_id, 'limit': 10}, expected_queue_ids=[second_id, first_id])
                    if ordered and 'thread/queue/reorder' in evidence['methods']:
                        evidence['methods']['thread/queue/reorder']['protocol_smoke']['effects']['reordered_items_observed'] = True
                    else:
                        evidence['methods'].pop('thread/queue/reorder', None)
                        evidence['blocked_methods']['thread/queue/reorder'] = {'status': 'blocked', 'reason': 'reorder_effect_not_observed'}
                    probe('thread/queue/delete', {'threadId': thread_id, 'queuedSubmissionId': first_id})
                rpc('turn/interrupt', {'threadId': thread_id, 'turnId': active['turn']['id']})
                probe('thread/queue/start', {'threadId': thread_id, 'queuedSubmissionId': second_id})
            for method in METHODS:
                if method not in evidence['methods'] and method not in evidence['blocked_methods']:
                    evidence['blocked_methods'][method] = {'status': 'blocked', 'reason': 'positive_fixture_prerequisite_unavailable'}
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            provider_release.set()
            if provider is not None:
                provider.shutdown()
                provider.server_close()
            evidence['cleanup'] = 'exited'
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--schemas', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    reviewed = json.loads((ROOT / 'tests/fixtures/protocol/codex_0.166.0_server_diagnostics.json').read_text())
    if hashlib.file_digest(args.binary.open('rb'), 'sha256').hexdigest() != reviewed['binary_sha256']:
        raise ValueError('Candidate binary does not match reviewed Codex 0.166.0 provenance')
    result = qualify(args.binary, args.schemas, args.output.parent)
    controls = qualify(args.binary, args.schemas, args.output.parent, local_provider=True)
    terminals = qualify(args.binary, args.schemas, args.output.parent, local_provider=True, terminal_fixture=True)
    controls['methods'].update({name: entry for name, entry in terminals['methods'].items()
                               if name not in controls['methods'] or entry['protocol_smoke']['effects']})
    controls['blocked_methods'].update(terminals['blocked_methods'])
    result['methods'].update({name: entry for name, entry in controls['methods'].items()
                              if name not in result['methods'] or entry['protocol_smoke']['effects']})
    result['blocked_methods'].update(controls['blocked_methods'])
    result['blocked_methods'] = {name: entry for name, entry in result['blocked_methods'].items()
                                 if name not in result['methods']}
    result['cleanup'] = 'exited' if result['cleanup'] == controls['cleanup'] == terminals['cleanup'] == 'exited' else 'failed'
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'passed': sorted(result['methods']), 'blocked': result['blocked_methods'], 'cleanup': result['cleanup']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
