"""本地实验室的请求边界、参数与并发回归；不依赖检查点。"""
import http.client
import json
import threading
import unittest

import numpy as np

from inference.profiling.pose_reference_lab import Lab, make_server


class LabHttpTests(unittest.TestCase):
    def setUp(self):
        self.lab=Lab.__new__(Lab)
        self.lab.packed=np.zeros((103,1,12))
        self.lab.token='test-token'
        self.lab.lock=threading.Lock()
        self.calls=[]
        def infer(refs,mode,edges):
            self.calls.append((refs,mode,edges))
            return {'references':list(refs),'mode':mode,'edges':edges}
        self.lab.infer=infer
        self.server=make_server(self.lab,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self,body,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            connection.request('POST','/api/run',json.dumps(body),{'X-Lab-Token':'test-token',**(headers or {})})
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:connection.close()

    def test_valid_request_deduplicates_and_preserves_baseline_contract(self):
        status,body=self.request({'references':[56,32,56],'mode':'proxy','edges':False})
        self.assertEqual(status,200)
        self.assertEqual(self.calls,[((),'proxy',True),((32,56),'proxy',False)])
        self.assertEqual(body['result']['references'],[32,56])

    def test_malformed_references_never_run_inference(self):
        for refs in ([True],[-1],[103],[1.5],'56'):
            self.assertEqual(self.request({'references':refs,'mode':'proxy','edges':True})[0],400)
        self.assertEqual(self.request([])[0],400)
        self.assertEqual(self.calls,[])

    def test_foreign_host_origin_and_missing_token_rejected(self):
        body={'references':[56],'mode':'proxy','edges':True}
        for headers in ({'Host':'foreign.example'},{'Origin':'https://foreign.example'},{'X-Lab-Token':''}):
            self.assertEqual(self.request(body,headers)[0],403)
        self.assertEqual(self.calls,[])

    def test_busy_response_and_failed_inference_release_lock(self):
        body={'references':[56],'mode':'history','edges':True}
        self.lab.lock.acquire()
        self.assertEqual(self.request(body)[0],409)
        self.lab.lock.release()
        def fail(refs,mode,edges):raise RuntimeError('test failure')
        self.lab.infer=fail
        self.assertEqual(self.request(body)[0],500)
        self.assertFalse(self.lab.lock.locked())


if __name__=='__main__':unittest.main()
