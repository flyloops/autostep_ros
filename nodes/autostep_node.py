#!/usr/bin/env python
from __future__ import print_function
import json
import threading
import roslib
import rospy
import std_msgs.msg

import scipy 
import scipy.interpolate

from autostep import Autostep

from autostep_ros.msg import MotionData
from autostep_ros.msg import TrackingData
from autostep_ros.srv import Command
from autostep_ros.srv import CommandResponse


class AutostepNode(object):


    def __init__(self,port='/dev/ttyACM0'):

        self.port = port
        self.step_mode = 'STEP_FS_128'
        self.fullstep_per_rev = 200
        self.gear_ratio = 2.0

        self.tracking_mode_gain = 5.0
        self.tracking_mode_is_first = False
        self.tracking_mode_first_update_t = 0.0
        self.tracking_mode_last_update_t = 0.0
        self.tracking_mode_position = 0.0
        self.tracking_mode_velocity = 0.0
        self.tracking_mode_position_start = 0.0

        self.initialize()
        self.enable()

        rospy.init_node('autostep')

        self.motion_pub = rospy.Publisher('motion_data', MotionData, queue_size=10) 
        self.tracking_sub = rospy.Subscriber('tracking_data', TrackingData, self.on_tracking_data_callback)

        self.command_srv_table = {
                'run'                   : self.on_run_command,
                'enable'                : self.on_enable_command,
                'release'               : self.on_release_command,
                'is_busy'               : self.on_is_busy_command,
                'move_to'               : self.on_move_to_command,
                'soft_stop'             : self.on_soft_stop_command,
                'set_position'          : self.on_set_position_command,
                'get_position'          : self.on_get_position_command,
                'set_move_mode'         : self.on_set_move_mode_command,
                'get_params'            : self.on_get_params_command,
                'print_params'          : self.on_print_params_command,
                'sinusoid'              : self.on_sinusoid_command,
                'run_trajectory'        : self.on_run_trajectory_command,
                'enable_tracking_mode'  : self.on_enable_tracking_mode_command,
                'disable_tracking_mode' : self.on_disable_tracking_mode_command,
                }
        self.command_srv = rospy.Service('command', Command, self.command_srv_callback)

        self.lock = threading.Lock()
        self.tracking_mode_enabled = False
        self.running_motion_cmd = False

    def initialize(self):
        self.autostep = Autostep(self.port)
        self.autostep.set_step_mode(self.step_mode) 
        self.autostep.set_fullstep_per_rev(self.fullstep_per_rev)
        self.autostep.set_gear_ratio(self.gear_ratio)
        self.autostep.set_move_mode_to_jog()
        self.have_sensor = False

    def enable(self):
        self.autostep.enable()
        self.enabled_flag = True

    def release(self):
        self.autostep.release()
        self.enabled_flag = False

    def command_srv_callback(self,req):
        args_dict = {}
        if req.args_json != '':
            args_json = req.args_json.replace('\{','{')
            args_json = args_json.replace('\}','}')
            args_dict = json.loads(args_json)
        ok = False
        try:
            command_method = self.command_srv_table[req.command]
            ok = True
        except KeyError:
            resp_dict = {'success': False,'message':'unknown command'}
        if ok:
            resp_dict = command_method(args_dict)

        return CommandResponse(json.dumps(resp_dict))

    def on_run_command(self,args_dict):
        ok = False
        velocity = 0.0
        resp_dict = {}
        try:
            velocity = args_dict['velocity']
            resp_dict['success'] = True
            resp_dict['message'] = ''
            ok = True
        except KeyError:
            resp_dict['success'] = False
            resp_dict['message'] = 'velocity argument missing'
        if ok:
            self.autostep.run(velocity)
        return resp_dict

    def on_enable_command(self,args_dict):
        self.enable()
        return {'success': True, 'message': ''}

    def on_release_command(self,args_dict):
        self.release()
        return {'success': True, 'message': ''}

    def on_move_to_command(self,args_dict):
        ok = False
        position = 0.0
        resp_dict = {}
        try:
            position = args_dict['position']
            resp_dict['success'] = True
            resp_dict['message'] = ''
            ok = True
        except KeyError:
            resp_dict['success'] = False
            resp_dict['message'] = 'position argument missing'
        if ok:
            self.autostep.move_to(position)
        return resp_dict

    def on_soft_stop_command(self,args_dict):
        self.autostep.soft_stop()
        return {'success': True, 'message': ''}

    def on_is_busy_command(self,args_dict):
        is_busy = self.autostep.is_busy()
        return {'success': True,'message': '','is_busy': is_busy}

    def on_get_position_command(self,args_dict):
        position = self.autostep.get_position()
        return {'success': True, 'message': '', 'position': position}

    def on_set_position_command(self,args_dict):
        ok = False
        position = 0.0
        resp_dict = {}
        try:
            position = args_dict['position']
            resp_dict['success'] = True
            resp_dict['message'] = ''
            ok = True
        except KeyError:
            resp_dict['success'] = False
            resp_dict['message'] = 'position argument missing'
        if ok:
            self.autostep.set_position(position)
        return resp_dict

    def on_sinusoid_command(self,args_dict):
        ok = True 
        param = {}
        resp_dict = {'message': ''}
        param_keys = ['amplitude', 'period', 'phase', 'offset', 'num_cycle']
        for key in param_keys:
            try:
                param[key] = args_dict[key]
            except KeyError:
                ok = False
                if len(resp_dict['message']) > 0:
                    resp_dict['message'] +=  ', '
                resp_dict['message'] += '{} argument missing'.format(key)
        if ok:

            def motion_data_callback(elapsed_time, position, setpoint, sensor):
                if not self.have_sensor:
                    sensor = 0.0
                header = std_msgs.msg.Header()
                header.stamp = rospy.Time.now()
                self.motion_pub.publish(MotionData(header, elapsed_time, position, setpoint, sensor))

            def motion_done_callback():
                with self.lock:
                    self.running_motion_cmd = False 

            # Launch sinusoid in separate thread
            thread_args = [param,motion_data_callback,motion_done_callback]
            motion_thread = threading.Thread(target=self.autostep.sinusoid,args=thread_args)
            with self.lock:
                self.running_motion_cmd = True
            motion_thread.start()

            resp_dict['success'] = True
        else:
            resp_dict['success'] = False
        return resp_dict


    def on_set_move_mode_command(self,args_dict):
        ok = False
        mode = ''
        resp_dict = {}
        try:
            mode = args_dict['mode']
            resp_dict['success'] = True
            resp_dict['message'] = ''
        except KeyError:
            resp_dict['success'] = False
            resp_dict['message'] = 'mode argument missing'
        if ok:
            if mode == 'max':
                self.autostep.set_move_mode_max()
            elif mode == 'jog':
                self.autostep.set_move_mode_jog()
            else:
                resp_dict['success'] = False
                resp_dict['message'] = "mode must be 'max' or 'jog'"
        return resp_dict 

    def on_print_params_command(self,args_dict):
        self.autostep.print_params()
        return {'success': True, 'message': ''} 

    def on_print_params_command(self,args_dict):
        self.autostep.print_params()
        return {'success': True, 'message': ''} 

    def on_get_params_command(self,args_dict):
        params = self.autostep.get_params()
        return {'success': True, 'message': '', 'params': params} 

    def on_run_trajectory_command(self, args_dict):
        resp_dict = {}
        try:
            position = args_dict['position']
        except KeyError:
            resp_dict['success'] = False
            resp_dict['message'] = 'position (array) argument missing'
            return resp_dict 

        position = scipy.array(position)
        dt = Autostep.TrajectoryDt

        velocity = scipy.zeros(position.shape)
        velocity[1:] = (position[1:] - position[:-1])/dt

        t = dt*scipy.arange(0,position.shape[0])
        t_done = t[-1] 

        position_func = scipy.interpolate.interp1d(t,position,kind='linear')
        velocity_func = scipy.interpolate.interp1d(t,velocity,kind='linear')

        def motion_data_callback(elapsed_time, position, setpoint):
            header = std_msgs.msg.Header()
            header.stamp = rospy.Time.now()
            self.motion_pub.publish(MotionData(header, elapsed_time, position, setpoint, 0.0))

        def motion_done_callback():
            with self.lock:
                self.running_motion_cmd = False

        # Launch sinusoid in separate thread 
        thread_args = [t_done,position_func,velocity_func,False,motion_data_callback] 
        motion_thread = threading.Thread(target=self.autostep.run_trajectory,args=thread_args)
        with self.lock:
            self.running_motion_cmd = True
        motion_thread.start()

        return {'success': True,'message': ''}

    def on_enable_tracking_mode_command(self, args_dict):
        with self.lock:
            self.autostep.run(0.0)
            self.autostep.set_move_mode_to_max()
            self.tracking_mode_enabled = True
            self.tracking_mode_is_first = True 
        return {'success': True, 'message': ''} 

    def on_disable_tracking_mode_command(self, args_dict):
        with self.lock:
            self.tracking_mode_enabled = False
            self.autostep.set_move_mode_to_jog()
            self.autostep.run(0.0)
        return {'success': True, 'message': ''} 

    def on_tracking_data_callback(self,msg):

        with self.lock:
            tracking_mode_enabled = self.tracking_mode_enabled

        if self.tracking_mode_enabled:
            if self.tracking_mode_is_first:
                self.tracking_mode_is_first = False
                self.tracking_mode_first_update_t = rospy.get_time() 
                self.tracking_mode_last_update_t = self.tracking_mode_first_update_t 
                self.tracking_mode_position_start = self.autostep.get_position()
                self.tracking_mode_position = self.tracking_mode_position_start
                
                predicted_position = self.tracking_mode_position
                self.tracking_mode_velocity = 0.0
                
            else:
                current_time = rospy.get_time()
                update_dt = current_time - self.tracking_mode_last_update_t

                predicted_position = self.tracking_mode_position + update_dt*self.tracking_mode_velocity
                position_error = msg.position - (predicted_position - self.tracking_mode_position_start)

                new_velocity = self.tracking_mode_gain*position_error + msg.velocity
                true_position = self.autostep.run_with_feedback(new_velocity)

                self.tracking_mode_position = true_position
                self.tracking_mode_velocity = new_velocity
                self.tracking_mode_last_update_t = current_time

            header = std_msgs.msg.Header()
            header.stamp = rospy.Time.now()

            elapsed_time = self.tracking_mode_last_update_t - self.tracking_mode_first_update_t
            self.motion_pub.publish(MotionData(header, elapsed_time, self.tracking_mode_position, predicted_position, 0.0))


    def run(self):
        while not rospy.is_shutdown():
            rospy.sleep(0.1)
        self.autostep.run(0.0)
    
# ---------------------------------------------------------------------------------------
if __name__ == '__main__':

    node = AutostepNode()
    node.run()

