#!/usr/bin/env python2

# Copyright 2017 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import cStringIO as StringIO
import json
import unittest

import make_json
import model
import time


class MakeJsonTest(unittest.TestCase):
    def setUp(self):
        self.db = model.Database(':memory:')

    def test_path_to_job_and_number(self):
        def expect(s, job, number):
            self.assertEqual(make_json.path_to_job_and_number(s), (job, number))

        expect('gs://kubernetes-jenkins/logs/some-build/123', 'some-build', 123)
        expect('gs://kubernetes-jenkins/logs/some-build/123asdf', 'some-build', None)
        expect('gs://kubernetes-jenkins/pr-logs/123/e2e-node/456', 'pr:e2e-node', 456)

        with self.assertRaises(ValueError):
            expect('gs://unknown-bucket/foo/123', None, None)

    def test_row_for_build(self):
        def expect(path, start, finish, results, **kwargs):
            expected = {
                'path': path,
                'test': [],
                'tests_failed': 0,
                'tests_run': 0,
            }
            expected.update(kwargs)
            row = make_json.row_for_build(path, start, finish, results)
            self.assertEqual(row, expected)

        path = 'gs://kubernetes-jenkins/logs/J/123'
        expect(path, None, None, [], job='J', number=123)
        expect(path, None, None, [], job='J', number=123)
        expect(path,
               {'timestamp': 10, 'node': 'agent-34'},
               {'timestamp': 15, 'result': 'SUCCESS', 'version': 'v1.2.3'},
               [],
               job='J', number=123,
               started=10, finished=15, elapsed=5,
               version='v1.2.3', result='SUCCESS', executor='agent-34',
               )
        expect(path, None, {'timestamp': 15, 'result': 'FAILURE', 'metadata': {'repo': 'ignored', 'pull': 'asdf'}}, [],
               result='FAILURE', job='J', number=123, finished=15,
               metadata=[{'key': 'pull', 'value': 'asdf'}, {'key': 'repo', 'value': 'ignored'}])
        expect(path, None, None, [
            '''<testsuite>
            <testcase name="t1" time="1.0"><failure>stacktrace</failure></testcase>
            <testcase name="t2" time="2.0"></testcase>
            </testsuite>'''],
            job='J', number=123,
            tests_run=2, tests_failed=1,
            test=[{'name': 't1', 'time': 1.0, 'failed': True, 'failure_text': 'stacktrace'}, {'name': 't2', 'time': 2.0}])

    def test_main(self):
        now = time.time()
        last_month = now - (60 * 60 * 24 * 30)
        junits = ['<testsuite><testcase name="t1" time="3.0"></testcase></testsuite>']

        def add_build(path, start, finish, result, junits):
            path = 'gs://kubernetes-jenkins/logs/%s' % path
            self.db.insert_build(path, {'timestamp': start}, {'timestamp': finish, 'result': result})
            # fake build rowid doesn't matter here
            self.db.insert_build_junits(hash(path), {'%s/artifacts/junit_%d.xml' % (path, n): junit for n, junit in enumerate(junits)})

        def expect(args, needles, negneedles):
            buf = StringIO.StringIO()
            opts = make_json.parse_args(args)
            make_json.main(self.db, opts, buf)
            result = buf.getvalue()

            # validate that output is newline-delimited JSON
            for line in result.split('\n'):
                if line.strip():
                    json.loads(line)

            # test for expected patterns / expected missing patterns
            for needle in needles:
                self.assertIn(needle, result)
            for needle in negneedles:
                self.assertNotIn(needle, result)

        add_build('some-job/123', last_month, last_month + 10, 'SUCCESS', junits)
        add_build('some-job/456', now - 10, now, 'FAILURE', junits)

        expect([], ['123', '456', 'SUCCESS', 'FAILURE'], [])  # everything
        expect([], [], ['123', '456', 'SUCCESS', 'FAILURE'])  # nothing

        expect(['--day'], ['456'], [])  # recent
        expect(['--days', '1'], [], ['456'])  # nothing (already emitted)

        add_build('some-job/457', now + 1, now + 11, 'SUCCESS', junits)
        expect(['--day'], ['457'], ['456'])  # latest (day)
        expect([], ['457'], ['456'])         # latest (all)

        expect(['--day', '--reset-emitted'], ['456', '457'], [])  # both (reset)
        expect([], [], ['123', '456', '457'])                     # reset only works for given day


if __name__ == '__main__':
    unittest.main()
