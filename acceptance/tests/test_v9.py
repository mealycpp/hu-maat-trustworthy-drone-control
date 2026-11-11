"""Offline safety/regression tests; never connects to live Gazebo or flies aircraft."""
import math
import threading
import unittest

# Imports gz stubs and controller modules without Gazebo.
import test_missions as previous
from mission_intents import parse
from hu_live_server import Service, normalize_utterance
from control_server_hu import Drone, Fleet, NAMES

class MockHU:
    def __init__(self,action='TAKEOFF',band='EXECUTE',conf=.95):
        self.action=action;self.band=band;self.conf=conf;self.count=0
    def parse(self,text):
        self.count+=1
        return {'band':self.band,'action':self.action,'targets':['Horus'],
                'params':{},'confidence':self.conf}

def svc(hu):
    service=Service(hu)
    service.record=lambda item:item
    return service

def fleet(mode='HOVER'):
    f=Fleet.__new__(Fleet)
    points=[(-2.2,-1.5),(0,-1.5),(2.2,-1.5),(-1.1,1.1),(1.1,1.1)]
    f.dr={name:Drone(name,x=x,y=y,z=1.65,mode=mode,init=True,last_seen=100,
                     target_x=x,target_y=y,target_z=1.65)
          for name,(x,y) in zip(NAMES,points)}
    f.lock=threading.RLock();f.fresh=lambda d:True;f.event=lambda *args,**kw:None
    return f

class TestV9(unittest.TestCase):
    def test_meaningful_mission_coverage(self):
        requests={
          'all form V shape':('formation','v',5),
          'all perform a circle formation':('formation','circle',5),
          'all form a grid':('formation','grid',5),
          'all except Ra form a line':('formation','line',4),
          'Horus turn 20 degrees right':('turn',-20,1),
          'all turn left 90 degrees':('turn',90,5),
          'Horus spin around yourself':('spin',1,1),
          'Horus go up 10 centimeters':('move',.1,1),
          'Anubis ascend fifty cm':('move',.5,1),
          'Ra descend 0.5 meters':('move',-.5,1),
          'Horus fly forward one meter':('move',1,1),
          'Thoth move backward 50 cm':('move',-.5,1),
          'Osiris shift right 0.8 m':('move',.8,1),
          'Anubis shift left twenty centimeters':('move',-.2,1),
          'all move forward one meter':('move',1,5),
          'all except Anubis move left 50 cm':('move',-.5,4),
          'all climb 30 centimeters':('move',.3,5),
        }
        for utterance,(kind,val,count) in requests.items():
            with self.subTest(utterance=utterance):
                result=parse(utterance)
                self.assertEqual(result['action'],kind)
                self.assertEqual(len(result['targets']),count)
                if kind=='formation':self.assertEqual(result['params']['shape'],val)
                if kind=='turn':self.assertEqual(result['params']['degrees'],val)
                if kind=='spin':self.assertEqual(result['params']['direction'],val)
                if kind=='move':
                    params=result['params'];self.assertAlmostEqual(list(params.values())[0],val)
    def test_reject_unknown_ambiguous_conflicting_and_unsafe(self):
        rejected=[
          'all orbit around the building','Horus dance','Horus backflip',
          'all patrol the arena','Horus go up five meters','Horus go down 5 cm',
          'Horus move left and right 20 cm','all move forward','all move up and down 20 cm',
          'Horus fly forward','all form a banana shape','form V','Horus or Ra land',
          'all except banana form line','all form v then land','Horus go up 10 a centimeter',
          'Horus go forward 0.8 m then land','Horus jump a meter',
          'Horus go up ten centimeters and then turn right',
          'hello drones','tell me a joke','everybody do something cool',
          'all go in a circle','Horus go in a circle',
        ]
        for utterance in rejected:
            with self.subTest(utterance=utterance):
                result=parse(utterance)
                self.assertIn('error',result,utterance)
    def test_original_primitive_direct_hu_route(self):
        # HU primitives to named drones, groups and exclusions go to the trained HU model
        # (the HU paper's demo), not the deterministic mission parser.
        for utterance in ('Horus take off','Horus land','Horus hover','Horus stop','all launch','all land','all hover',
                          'Horus please take off','Ra please land','All, take off','Everyone except Ra, land',
                          'Horus and Thoth, take off','all drones hover please','Ra, Thoth and Osiris land'):
            with self.subTest(utterance=utterance):
                self.assertIsNone(parse(utterance))
    def test_wrong_model_label_refused_even_high_confidence(self):
        model=MockHU('STOP',conf=.99);service=svc(model)
        service.publish=lambda *a,**kw:self.fail('Dispatched wrong HU action')
        result=service.command('Horus take off')
        self.assertEqual(result['band'],'REFUSE')
        self.assertIn('mismatch',result['reason'])
        self.assertEqual(model.count,1)
    def test_real_hu_still_executes_matching_label(self):
        model=MockHU('TAKEOFF',conf=.95);service=svc(model)
        sent=[];service.publish=lambda *a,**kw:sent.append(a) or {'ok':True}
        result=service.command('Horus take off')
        self.assertTrue(result['dispatched']);self.assertEqual(result['decision_source'],'REAL HU MODEL')
        self.assertEqual(sent[0][0],'takeoff');self.assertEqual(sent[0][1],['Horus'])
    def test_all_form_does_not_call_hu(self):
        model=MockHU('STOP',conf=.99);service=svc(model)
        result=service.command('all form V shape')
        self.assertEqual(result['band'],'CONFIRM')
        self.assertFalse(result['dispatched']);self.assertEqual(model.count,0)
    def test_unsupported_missions_do_not_call_hu(self):
        model=MockHU();service=svc(model)
        for utterance in ('Horus dance','all orbit'):
            result=service.command(utterance)
            self.assertEqual(result['band'],'REFUSE')
        self.assertEqual(model.count,0)
    def test_unrecognized_sentence_shows_hu_but_never_executes(self):
        # HU is consulted (its REFUSE class is part of the demo), but even a confident
        # EXECUTE prediction on a sentence with no explicit flight verb is refused.
        model=MockHU('TAKEOFF',band='EXECUTE',conf=.99);service=svc(model)
        service.publish=lambda *a,**kw:self.fail('Dispatched an unrecognized sentence')
        for utterance in ('tell me a joke','this project is really taking off'):
            result=service.command(utterance)
            self.assertEqual(result['band'],'REFUSE');self.assertFalse(result['dispatched'])
            self.assertEqual(result['decision_source'],'REAL HU MODEL')
        self.assertEqual(model.count,2)
    def test_stop_is_hover_not_landing(self):
        model=MockHU();service=svc(model)
        sent=[];service.publish=lambda *args,**kwargs:sent.append((args,kwargs)) or {'ok':True}
        result=service.command('all stop')
        self.assertTrue(result['dispatched'])
        self.assertEqual(result['action'],'HOVER')
        self.assertEqual(sent[0][0][0],'emergency_hover')
        self.assertTrue(sent[0][1]['symbolic'])
        self.assertEqual(model.count,0)
    def test_bare_land_does_not_land_whole_fleet(self):
        result=svc(MockHU()).command('land')
        self.assertEqual(result['band'],'REFUSE')
    def test_simulated_ground_stop_does_not_takeoff(self):
        f=fleet(mode='READY');result=f.command('emergency_hover')
        self.assertTrue(result['ok'])
        self.assertTrue(all(d.mode=='READY' for d in f.dr.values()))
        self.assertTrue(all(abs(d.target_z-1.65)<1e-6 for d in f.dr.values()))
    def test_move_collision_refused_without_mutation(self):
        f=fleet()
        before=(f.dr['Horus'].target_x,f.dr['Horus'].target_y,f.dr['Horus'].target_z)
        with self.assertRaisesRegex(ValueError,'separation'):
            f.command('move',['Horus'],{'forward_m':2.0})
        self.assertEqual((f.dr['Horus'].target_x,f.dr['Horus'].target_y,f.dr['Horus'].target_z),before)
    def test_move_valid_short_single(self):
        f=fleet();f.command('move',['Horus'],{'forward_m':.5})
        self.assertEqual(f.dr['Horus'].mode,'MOVE')
        self.assertAlmostEqual(f.dr['Horus'].target_x,-1.7)
    # --- swarm travel (replaces V9.1 test_fleetwide_move_disabled) ---
    def test_swarm_move_is_rigid_translation(self):
        f=fleet();before={n:(d.x,d.y,d.z) for n,d in f.dr.items()}
        f.command('move',list(NAMES),{'forward_m':.5,'dz':.2})
        for n,d in f.dr.items():
            self.assertEqual(d.mode,'MOVE')
            self.assertAlmostEqual(d.target_x,before[n][0]+.5);self.assertAlmostEqual(d.target_y,before[n][1])
            self.assertAlmostEqual(d.target_z,before[n][2]+.2)
    def test_swarm_right_is_arena_minus_y(self):
        f=fleet();y0=f.dr['Ra'].y
        f.command('move',list(NAMES),{'right_m':.4})
        self.assertAlmostEqual(f.dr['Ra'].target_y,y0-.4)
    def test_swarm_refuses_grounded_member_without_mutation(self):
        f=fleet();f.dr['Thoth'].mode='READY'
        before={n:(d.target_x,d.target_y,d.target_z,d.mode) for n,d in f.dr.items()}
        with self.assertRaisesRegex(ValueError,'Thoth'):
            f.command('move',['Horus','Ra','Thoth'],{'forward_m':.5})
        self.assertEqual({n:(d.target_x,d.target_y,d.target_z,d.mode) for n,d in f.dr.items()},before)
    def test_swarm_refuses_unsettled_member(self):
        f=fleet();f.dr['Ra'].target_x+=.5
        with self.assertRaisesRegex(ValueError,'settled'):
            f.command('move',['Horus','Ra'],{'forward_m':.3})
    def test_swarm_refuses_path_near_stationary_aircraft(self):
        f=fleet()  # Anubis (-1.1,1.1) +1.5 m in x ends 0.7 m from the stationary Osiris (1.1,1.1)
        with self.assertRaisesRegex(ValueError,'within 1.0 m of Osiris'):
            f.command('move',['Anubis','Horus'],{'forward_m':1.5})
        f.command('move',['Anubis','Horus'],{'forward_m':1.0})  # 1.2 m clearance: accepted
    def test_swarm_refuses_out_of_bounds_without_clamping(self):
        f=fleet()
        with self.assertRaisesRegex(ValueError,'arena'):
            f.command('move',list(NAMES),{'forward_m':1.5})
    # --- staggered, layered takeoff for aircraft parked too close ---
    def test_close_pair_takeoff_is_layered_not_refused(self):
        f=fleet(mode='READY');f.dr['Ra'].x=-1.6;f.dr['Ra'].y=-1.5  # 0.6 m from Horus
        for d in f.dr.values():d.z=.05
        f.command('takeoff',list(NAMES),{'altitude':1.7})
        modes={n:d.mode for n,d in f.dr.items()}
        self.assertEqual(sorted([modes['Horus'],modes['Ra']]),['QUEUED','TAKEOFF'])
        first,second=('Horus','Ra') if modes['Horus']=='TAKEOFF' else ('Ra','Horus')
        self.assertAlmostEqual(f.dr[first].target_z,2.6)
        self.assertEqual(f.dr[second].queue_after,(first,));self.assertAlmostEqual(f.dr[second].queue_alt,1.7)
        self.assertTrue(all(modes[n]=='TAKEOFF' for n in ('Thoth','Anubis','Osiris')))
    def test_three_crowded_aircraft_refused(self):
        f=fleet(mode='READY')
        for n,(x,y) in zip(('Horus','Ra','Thoth'),[(0,0),(.5,0),(.25,.4)]):f.dr[n].x=x;f.dr[n].y=y
        for d in f.dr.values():d.z=.05
        with self.assertRaisesRegex(ValueError,'too close together'):
            f.command('takeoff',['Horus','Ra','Thoth'])
        self.assertTrue(all(d.mode=='READY' for d in f.dr.values()))
    def test_stop_cancels_queued_launch(self):
        f=fleet(mode='READY');f.dr['Ra'].x=-1.6;f.dr['Ra'].y=-1.5
        for d in f.dr.values():d.z=.05
        f.command('takeoff',['Horus','Ra'])
        q=next(n for n in ('Horus','Ra') if f.dr[n].mode=='QUEUED')
        f.command('emergency_hover')
        self.assertEqual(f.dr[q].mode,'GROUNDED')
    def test_swarm_refuses_faulted_member(self):
        f=fleet();f.dr['Osiris'].fault=True
        with self.assertRaisesRegex(ValueError,'fault'):
            f.command('move',['Anubis','Osiris'],{'forward_m':.3})
    def test_no_out_of_bounds_clamping(self):
        f=fleet();f.dr['Horus'].x=3.2;f.dr['Horus'].target_x=3.2
        with self.assertRaisesRegex(ValueError,'arena'):
            f.command('move',['Horus'],{'forward_m':.5})
    def test_existing_fault_refusal_not_bypassed(self):
        f=fleet();f.dr['Ra'].fault=True
        with self.assertRaisesRegex(ValueError,'fault'):
            f.command('hover',['Ra'])
    def test_voice_aliases_suggest_review(self):
        normalized=normalize_utterance('horror please take off','voice')
        self.assertEqual(normalized,'Horus please take off')
        self.assertEqual(normalize_utterance('old take off','voice'),'all take off')

if __name__=='__main__':unittest.main(verbosity=2)
