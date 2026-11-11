import importlib.util,itertools,math,sys,types,threading,unittest
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
from formation_planner import slots,assign,MIN_SEP
from mission_intents import parse,NAMES

for name in ('gz','gz.transport13','gz.msgs10','gz.msgs10.boolean_pb2','gz.msgs10.odometry_pb2','gz.msgs10.twist_pb2'):
    sys.modules[name]=types.ModuleType(name)
sys.modules['gz.transport13'].Node=type('Node',(),{})
for mod,cls in [('gz.msgs10.boolean_pb2','Boolean'),('gz.msgs10.odometry_pb2','Odometry'),('gz.msgs10.twist_pb2','Twist')]:
    setattr(sys.modules[mod],cls,type(cls,(),{}))
spec=importlib.util.spec_from_file_location('control_server_hu',root/'control_server_hu.py')
controller=importlib.util.module_from_spec(spec);sys.modules[spec.name]=controller;spec.loader.exec_module(controller)
spec=importlib.util.spec_from_file_location('hu_live_server',root/'hu_live_server.py')
bridge=importlib.util.module_from_spec(spec);sys.modules[spec.name]=bridge;spec.loader.exec_module(bridge)

class MockHU:
    count=0
    def parse(self,s):
        self.count+=1
        return {'band':'REFUSE','action':'STOP','targets':[],'confidence':0.01}

class Cases(unittest.TestCase):
    def test_v_intercept_does_not_call_hu(self):
        hu=MockHU();svc=bridge.Service(hu)
        svc.record=lambda e:e
        response=svc.command('all form v shape')
        self.assertEqual(response['band'],'CONFIRM')
        self.assertEqual(response['action'],'FORMATION')
        self.assertEqual(response['params']['shape'],'v')
        self.assertEqual(hu.count,0)
        self.assertFalse(response['dispatched'])
    def test_voice_confirmation_mission(self):
        hu=MockHU();svc=bridge.Service(hu)
        svc.record=lambda e:e
        received=[]
        svc.publish_mission=lambda action,targets,params:received.append((action,targets,params)) or {'ok':True}
        r=svc.command('all perform V shape',source='voice',voice_reviewed=True)
        self.assertEqual(received,[])
        ack=svc.confirm(r['cmd_id'])
        self.assertTrue(ack['ok'])
        self.assertEqual(received[0][0],'formation')
        self.assertEqual(len(received[0][1]),5)
        with self.assertRaises(ValueError):svc.confirm(r['cmd_id'])
    def test_unsupported_refused_before_hu(self):
        hu=MockHU();svc=bridge.Service(hu);svc.record=lambda e:e
        for cmd in ('all orbit around the building','all go in circle','form V','turn around right'):
            r=svc.command(cmd)
            self.assertEqual(r['band'],'REFUSE',cmd)
            self.assertFalse(r['dispatched'])
        self.assertEqual(hu.count,0)
    def test_base_uses_real_hu(self):
        hu=MockHU();svc=bridge.Service(hu);svc.record=lambda e:e
        svc.command('Horus take off')
        self.assertEqual(hu.count,1)
    def test_turn_target(self):
        self.assertEqual(parse('Horus turn around 20 degrees right')['params']['degrees'],-20)
        self.assertEqual(len(parse('all turn left 90 degrees')['targets']),5)
        self.assertEqual(parse('all except Ra form a circle')['targets'],['Horus','Thoth','Anubis','Osiris'])
    def test_spin_360(self):
        self.assertEqual(parse('Horus spin around yourself')['params']['direction'],1)
    def test_shape_catalogue(self):
        shapes=['v','inverted_v','wedge','line','column','circle','pentagon','diamond','square','grid','triangle','cross','x','arc','fan','zigzag','staggered','two_rows','left_echelon','right_echelon','arrow','t','l','perimeter','relay_chain']
        self.assertGreaterEqual(len(shapes),20)
        for sh in shapes:
            for n in range(2,6):
                pts=slots(sh,n)
                self.assertEqual(len(pts),n)
                for a,b in itertools.combinations(pts,2):
                    self.assertGreaterEqual(math.dist(a,b),MIN_SEP)
    def fleet(self):
        f=controller.Fleet.__new__(controller.Fleet)
        xy=[(-2.2,-1.5),(0,-1.5),(2.2,-1.5),(-1.1,1.1),(1.1,1.1)]
        f.dr={n:controller.Drone(n,x=x,y=y,z=1.65,mode='HOVER',init=True,last_seen=100.) for n,(x,y) in zip(controller.NAMES,xy)}
        f.lock=threading.RLock();f.fresh=lambda d:True;f.event=lambda *a:None
        return f
    def test_formation_controller(self):
        f=self.fleet()
        result=f.command('formation',controller.NAMES,{'shape':'v'},source='MISSION CONFIRMED')
        self.assertEqual(result['shape'],'v')
        self.assertTrue(all(d.mode=='FORMATION' for d in f.dr.values()))
    def test_grounded_no_formation(self):
        f=self.fleet();f.dr['Horus'].mode='GROUNDED';f.dr['Horus'].z=.05
        with self.assertRaisesRegex(ValueError,'Take off'):f.command('formation',controller.NAMES,{'shape':'circle'})
    def test_fault_blocks_formation(self):
        f=self.fleet();f.dr['Ra'].fault=True
        with self.assertRaisesRegex(ValueError,'Faulted'):f.command('formation',controller.NAMES,{'shape':'v'})
    def test_collision_checker(self):
        f=self.fleet()
        pts=slots('line',5);self.assertEqual(len(assign(controller.NAMES,f.dr,pts)),5)
        f.dr['Ra'].x=f.dr['Horus'].x;f.dr['Ra'].y=f.dr['Horus'].y
        with self.assertRaises(ValueError):assign(controller.NAMES,f.dr,pts)
    def test_spin_controller(self):
        f=self.fleet()
        result=f.command('spin',['Horus'],{'direction':1})
        self.assertTrue(result['ok'])
        self.assertEqual(f.dr['Horus'].spin_segments,4)
        self.assertEqual(f.dr['Horus'].mode,'SPIN')
    def test_no_float_inference_from_mission(self):
        r=bridge.Service(MockHU()).command('all form v',source='typed')
        self.assertNotIn('confidence',r)
        self.assertEqual(r['decision_source'],'DETERMINISTIC MISSION PLANNER')

if __name__=='__main__':unittest.main(verbosity=2)
