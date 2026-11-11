"""Authenticated supervisor endpoint contract tests; no Gazebo transport used."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib import request,error
from unittest.mock import patch
import test_missions
import control_server_hu as ctrl

class StubFleet:
    def __init__(self):self.commands=[];self.hu_connected=False
    def command(self,action,targets,params=None,source=''):
        self.commands.append((action,targets,params,source))
        return {'ok':True,'action':action,'targets':targets}
    def status(self):return {'drones':[]}
    def event(self,*args):pass

class ServerIntegration(unittest.TestCase):
    def setUp(self):
        self.fleet=StubFleet();ctrl.Handler.fleet=self.fleet
        self.server=ThreadingHTTPServer(('127.0.0.1',0),ctrl.Handler)
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.token_patch=patch.object(ctrl,'token',return_value='test-bridge-token');self.token_patch.start()
    def tearDown(self):
        self.token_patch.stop();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def post(self,route,body,auth=False):
        payload=json.dumps(body).encode('utf-8')
        headers={'Content-Type':'application/json'}
        if auth:headers['X-HU-Token']='test-bridge-token'
        req=request.Request(self.url+route,data=payload,headers=headers)
        try:
            with request.urlopen(req,timeout=3) as response:return response.status,json.load(response)
        except error.HTTPError as ex:return ex.code,json.load(ex)
    def test_unauthenticated_mission_refused(self):
        req={'action':'formation','targets':['Horus','Ra'],'params':{'shape':'v'},
             'operator_confirmed':True,'planner':'HU-MISSION-V6'}
        code,_=self.post('/api/mission/dispatch',req)
        self.assertEqual(code,403);self.assertFalse(self.fleet.commands)
    def test_unconfirmed_mission_refused(self):
        req={'action':'takeoff','targets':['Horus'],'params':{},
             'operator_confirmed':False,'planner':'HU-MISSION-V6'}
        code,_=self.post('/api/mission/dispatch',req,auth=True)
        self.assertEqual(code,409);self.assertFalse(self.fleet.commands)
    def test_confirmed_mission_dispatched_by_deterministic_route(self):
        req={'action':'move','targets':['Horus'],'params':{'dz':.1},
             'operator_confirmed':True,'planner':'HU-MISSION-V6'}
        code,result=self.post('/api/mission/dispatch',req,auth=True)
        self.assertEqual(code,200);self.assertTrue(result['ok'])
        self.assertEqual(self.fleet.commands[0][0],'move')
        self.assertEqual(self.fleet.commands[0][3],'MISSION CONFIRMED')
        self.assertFalse(self.fleet.hu_connected) # not a trained HU dispatch
    def test_symbolic_hover_authorized_without_model_threshold(self):
        req={'action':'emergency_hover','targets':list(ctrl.NAMES),'params':{},
             'symbolic_emergency':True,'confidence':1}
        code,result=self.post('/api/hu/dispatch',req,auth=True)
        self.assertEqual(code,200);self.assertTrue(result['ok'])
        self.assertEqual(self.fleet.commands[0][0],'emergency_hover')
    def test_unauthorized_hu_refused(self):
        req={'action':'takeoff','targets':['Horus'],'params':{},'confidence':1}
        code,_=self.post('/api/hu/dispatch',req)
        self.assertEqual(code,403);self.assertFalse(self.fleet.commands)
    def test_hu_threshold_unchanged(self):
        req={'action':'takeoff','targets':['Horus'],'params':{},'confidence':.70}
        code,_=self.post('/api/hu/dispatch',req,auth=True)
        self.assertEqual(code,409)
        self.assertFalse(self.fleet.commands)

if __name__=='__main__':unittest.main(verbosity=2)
