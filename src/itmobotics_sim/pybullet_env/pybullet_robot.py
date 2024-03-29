import sys, os
import time
import copy
from json import tool
from ntpath import join
import uuid

import numpy as np
from spatialmath import SE3, SO3
from spatialmath.base import r2q

import pybullet as p
import pybullet_utils.bullet_client as bc
from pybullet_utils import urdfEditor as ed
from scipy.spatial.transform import Rotation as R

from itmobotics_sim.utils import robot
from itmobotics_sim.utils import math

from itmobotics_sim.pybullet_env.urdf_editor import URDFEditor


class SimulationException(Exception):
    pass

class PyBulletRobot(robot.Robot):
    def __init__(self, 
        urdf_filename: str,
        pybullet_client: bc.BulletClient = None,
        base_transform: SE3 = SE3(),
        joint_controller_params: dict = None,
        use_self_collision = True,
        additional_path: list[str] = [],
        fixed_base: bool = True
    ):
        super().__init__(urdf_filename, base_transform)
        self.__p = pybullet_client
        if self.__p is None:
            self.__p = bc.BulletClient(connection_mode=p.DIRECT)
        self.__base_urdf_filename = urdf_filename
        self.__robot_id = None
        self.__initialized = False
        self.__actuators_name_list = []
        self.__actuators_id_list = []
        self.__num_actuators = 0
        self.__joint_id_for_link = {}

        self.__additional_path = additional_path
        self.__external_models = {}
        self.__tool_list = []
        self.__cameras = {}

        self.__use_self_collision = use_self_collision
        self.__fixed_base = fixed_base

        self.__joint_limits: robot.JointLimits = None
        # print(self.__p)
        self.reset()

        self.__joint_controller_params = {
            'kp': self.__joint_limits.limit_torques[1]/10.0,
            'kd': self.__joint_limits.limit_torques[1]/10.0,
            'max_torque': self.__joint_limits.limit_torques[1]
        }
        if joint_controller_params:
            self.joint_controller_params = joint_controller_params

    def __del__(self):
        for m in self.__external_models.keys():
            if os.path.exists(self.__external_models[m]["urdf_filename"]):
                os.remove(self.__external_models[m]["urdf_filename"])
    
    def connect_tool(self, tool_name: str, external_urdf_filename: str, root_link: str, tf: SE3 = SE3(), save = False):
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')

        main_editor = URDFEditor(self._urdf_filename, self.__additional_path)
        child_editor = URDFEditor(external_urdf_filename, self.__additional_path)
        self._urdf_filename = main_editor.urdf_filename
        
        main_editor.joinURDF(child_editor, root_link, tf.A)

        head = os.path.split(self._urdf_filename)[0]
        newname = str(uuid.uuid4()) + '_tmp'+ '.urdf'
        newpath = os.path.join(head, newname)
        self._urdf_filename = newpath
        main_editor.save(self._urdf_filename)
        self.__external_models[tool_name] = {"urdf_filename": self._urdf_filename, "root_link": root_link, "tf": tf, "save": save}
        self.__tool_list.append(tool_name)

        jj = robot.JointState(self.__num_actuators)
        self._update_joint_state(jj)
        
        self.reset()
        self.reset_joint_state(jj)
        self.__reset_tools()
    
    def remove_tool(self, tool_name):
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')

        for i in range(len(self.__tool_list)-1, -1, -1):
            last_tool = self.__tool_list[i]
            self.__tool_list = self.__tool_list[:i]
            if last_tool == tool_name:
                break
        if len(self.__tool_list)==0:
            self._urdf_filename = self.__base_urdf_filename
        else:
            self._urdf_filename = self.__external_models[self.__tool_list[-1]]["urdf_filename"]

        jj = robot.JointState(self.__num_actuators)
        self._update_joint_state(jj)

        self.reset()
        self.reset_joint_state(jj)
        self.__reset_tools()
    
    def __reset_tools(self):
        for i in range(0, len(self.__tool_list)):
            t = self.__tool_list[i]
            if not self.__external_models[t]["save"]:
                # for k in self.__tool_list[i:]:
                #     self.__external_models.pop(k, None)
                self.__tool_list = self.__tool_list[:i]
                if len(self.__tool_list)==0:
                    self._urdf_filename = self.__base_urdf_filename
                else:
                    self._urdf_filename = self.__external_models[self.__tool_list[-1]]["urdf_filename"]
                break
    
    def reset_joint_state(self, jstate: robot.JointState):
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')
        assert self._joint_state.num_joints == jstate.num_joints,\
            "Can not set joint state, because given state has invalid size, expected {}, but given {}".format(
                self._joint_state.num_joints, jstate.num_joints
            )
        self._joint_state = jstate
        for i in range(self.__num_actuators):
            self.__p.resetJointState(self.__robot_id, self.__actuators_id_list[i], self._joint_state.joint_positions[i], self._joint_state.joint_velocities[i])
    
    def reset_ee_state(self, eestate: robot.EEState, initial_state: robot.JointState = None) -> bool:
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')
        ref_frame = eestate.ref_frame
        base_ee_state = copy.deepcopy(eestate)
        if ref_frame!='global':
            refFrameState = self.__p.getLinkState(self.__robot_id, self.__joint_id_for_link[ref_frame], computeForwardKinematics=1)
            _,_,_,_, ref_frame_pos, ref_frame_rot = refFrameState
            in_base_tf =  SE3(*ref_frame_pos)@ SE3(SO3(R.from_quat(ref_frame_rot).as_matrix(), check=False))
            base_ee_state.tf = in_base_tf @ base_ee_state.tf
            base_ee_state.twist = np.kron(np.eye(2,dtype=int), in_base_tf.R) @ base_ee_state.twist

        position = tuple(base_ee_state.tf.t)
        orientation = tuple(r2q(base_ee_state.tf.R,order='xyzs'))
        js = robot.JointState(self.__num_actuators)
        self._update_joint_state(js)
        new_proposed_q = np.zeros(js.joint_positions.shape)
        limit_range = self.joint_limits.limit_positions[1]-self.joint_limits.limit_positions[0]
        if initial_state is None:
            initial_state = robot.JointState.from_position(new_proposed_q)
        self.reset_joint_state(initial_state)
        new_proposed_q = np.array(
            list(
                self.__p.calculateInverseKinematics(
                    self.__robot_id, self.__joint_id_for_link[eestate.ee_link],
                    position,
                    orientation,
                    maxNumIterations=10000,
                    residualThreshold=1e-6,
                    restPoses = list(initial_state.joint_positions),
                    lowerLimits = list(self.joint_limits.limit_positions[0]),
                    upperLimits = list(self.joint_limits.limit_positions[1])
                )
            )
        )
        
        for i in range(new_proposed_q.shape[0]):
            if new_proposed_q[i]<self.joint_limits.limit_positions[0][i]:
                new_proposed_q[i] =  new_proposed_q[i]+ np.pi*2
            elif new_proposed_q[i]>self.joint_limits.limit_positions[1][i]:
                new_proposed_q[i] =  new_proposed_q[i]- np.pi*2
            else:
                new_proposed_q[i] =  new_proposed_q[i]
            if new_proposed_q[i]<self.joint_limits.limit_positions[0][i] or new_proposed_q[i]>self.joint_limits.limit_positions[1][i]:
                return False

        js.joint_positions = new_proposed_q
        js.joint_velocities = np.linalg.pinv(self.jacobian(js.joint_positions, eestate.ee_link, eestate.ref_frame)) @ base_ee_state.twist
        self.reset_joint_state(js)
        self._update_joint_state(js)
        return True


    def jacobian(self, joint_pose: np.ndarray, ee_link: str, ref_frame: str ='global') -> np.ndarray:
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')
        Jv = np.zeros((3, len(joint_pose)))
        Jw = np.zeros((3, len(joint_pose)))

        if ee_link!='global' and ee_link!=self.__p.getBodyInfo(self.__robot_id)[0].decode('utf-8'):
            # Please call self.__p.stepSimulation before using self.__p.calculateJacobian.
            jac_t, jac_r = self.__p.calculateJacobian(
                self.__robot_id, self.__joint_id_for_link[ee_link], [0,0,0],
                list(joint_pose), list(np.zeros(joint_pose.shape)),
                list(np.zeros(joint_pose.shape))
            )
            Jv = np.asarray(jac_t)[:, -self.__num_actuators:]
            Jw = np.asarray(jac_r)[:, -self.__num_actuators:]

        if ref_frame =='global':
            Jv = self._base_transform.R @ Jv
            Jw = self._base_transform.R @ Jw
        elif ref_frame==self.__p.getBodyInfo(self.__robot_id)[0].decode('utf-8'):
            pass
        else:
            # print(self.__p.getBodyInfo(self.__robot_id)[0].decode('utf-8'))
            refFrameState = self.__p.getLinkState(self.__robot_id, self.__joint_id_for_link[ref_frame], computeForwardKinematics=1)
            _,_,_,_, ref_frame_pos, ref_frame_rot = refFrameState
            in_base_tf =  SE3(*ref_frame_pos)@ SE3(SO3(R.from_quat(ref_frame_rot).as_matrix(), check=False))
            Jv = in_base_tf.R.T @ Jv
            Jw = in_base_tf.R.T @ Jw

        J = np.concatenate((Jv,Jw), axis=0)
        return J
    
    def _send_eecontrol_position(self, position: np.ndarray) -> bool:
        raise SimulationException('Robot does not support this type of control')

    def _send_eecontrol_velocity(self, velocity: np.ndarray) -> bool:
        raise SimulationException('Robot does not support this type of control')

    def _send_jointcontrol_velocity(self, velocity: np.ndarray) -> bool:
        if not self.__initialized:
            return False
        
        self.__recalc_torque = None
        self.__p.setJointMotorControlArray(self.__robot_id,
            self.__actuators_id_list,
            self.__p.VELOCITY_CONTROL,
            targetVelocities=velocity.tolist(),
            forces = self.__joint_controller_params['max_torque'].tolist()
        )
        return True
    
    def _send_jointcontrol_position(self, position: np.ndarray) -> bool:
        if not self.__initialized:
            return False

        self.__recalc_torque = None
        self.__p.setJointMotorControlArray(self.__robot_id,
            self.__actuators_id_list,
            self.__p.POSITION_CONTROL,
            targetPositions=position.tolist(),
            targetVelocities=np.zeros(self.__num_actuators).tolist(),
            positionGains=self.__joint_controller_params['kp'].tolist(),
            velocityGains=self.__joint_controller_params['kd'].tolist(),
            forces = self.__joint_controller_params['max_torque'].tolist()
        )
        return True
    
    def _send_jointcontrol_torque(self, torque: np.ndarray) -> bool:
        if not self.__initialized:
            return False
        
        self.__recalc_torque = torque
        self.__p.setJointMotorControlArray(self.__robot_id, self.__actuators_id_list,
            self.__p.VELOCITY_CONTROL, 
            forces=np.zeros(self.__num_actuators))
        self.__p.setJointMotorControlArray(self.__robot_id, 
            self.__actuators_id_list,
            controlMode = self.__p.TORQUE_CONTROL, 
            forces = torque.tolist()
        )
        return True
    
    def _update_ee_state(self, tool_state: robot.EEState):
        # print(p.getNumJoints(self.__robot_id))
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')

        link_frame_pos = np.zeros(3)
        link_frame_rot = np.array([0,0,0,1])

        if tool_state.ee_link!='global' and tool_state.ee_link!=self.__p.getBodyInfo(self.__robot_id)[0].decode('utf-8'):
            eeState = self.__p.getLinkState(self.__robot_id, self.__joint_id_for_link[tool_state.ee_link], computeLinkVelocity=1, computeForwardKinematics=1)
            _,_,_,_, link_frame_pos, link_frame_rot, link_frame_pos_vel, link_frame_rot_vel = eeState
        
        tool_state.tf = SE3(*link_frame_pos) @ SE3(SO3(R.from_quat(link_frame_rot).as_matrix(), check=False))
        tool_state.twist = np.concatenate([link_frame_pos_vel, link_frame_rot_vel])
        
        pb_joint_state = self.__p.getJointState(self.__robot_id, self.__joint_id_for_link[tool_state.ee_link])
        tool_state.force_torque = np.array(pb_joint_state[2])

        if tool_state.ref_frame == self.__p.getBodyInfo(self.__robot_id)[0].decode('utf-8'):
            reference_tf = self._base_transform
            tool_state.tf = (reference_tf).inv() @ tool_state.tf
            rotation_6d = np.kron(np.eye(2,dtype=int), R.from_quat(ref_frame_rot).inv().as_matrix())
            tool_state.twist = rotation_6d @ (tool_state.twist - ref_frame_twist)
            
        elif tool_state.ref_frame != 'global':
            refFrameState = self.__p.getLinkState(self.__robot_id, self.__joint_id_for_link[tool_state.ref_frame], computeLinkVelocity=1, computeForwardKinematics=1)
            _,_,_,_, ref_frame_pos, ref_frame_rot, ref_frame_pos_vel, ref_frame_rot_vel  = refFrameState
            ref_frame_twist = np.concatenate([ref_frame_pos_vel, ref_frame_rot_vel])

            tool_state.tf = (SE3(*ref_frame_pos) @ SE3(SO3(R.from_quat(ref_frame_rot).as_matrix(), check=False))).inv() @ tool_state.tf
            rotation_6d = np.kron(np.eye(2,dtype=int), R.from_quat(ref_frame_rot).inv().as_matrix())
            tool_state.twist = rotation_6d @ (tool_state.twist - ref_frame_twist)
            # tool_state.force_torque = rotation_6d @ tool_state.force_torque
    
    def _update_joint_state(self, joint_state: robot.JointState):
        if not self.__initialized:
            raise SimulationException('Robot was not initialized')
        pb_joint_state = self.__p.getJointStates(self.__robot_id, self.__actuators_id_list)
        joint_state.joint_positions = np.array([state[0] for state in pb_joint_state])
        joint_state.joint_velocities = np.array([state[1] for state in pb_joint_state])
        if self.__recalc_torque is None:
            joint_state.joint_torques = np.array([state[3] for state in pb_joint_state])
        else:
            joint_state.joint_torques = self.__recalc_torque
    
    def __remove_robot_body(self):
        if self.__robot_id is None:
            return
        elif not self.__initialized:
            raise SimulationException('Robot was not initialized')
        else:
            self.__p.removeBody(self.__robot_id)
            self.__robot_id = None

    def reset(self):
        self.__remove_robot_body()

        self.__base_pose = self._base_transform.t.tolist() # World position [x,y,z]
        self.__base_orient = R.from_matrix(self._base_transform.R).as_quat().tolist() # Quaternioun [x,y,z,w]
        # print("Loading urdf ", self._urdf_filename)

        if self.__use_self_collision:
            flags_bullet = self.__p.URDF_USE_SELF_COLLISION

        self.__robot_id = self.__p.loadURDF(
            self._urdf_filename,
            basePosition=self.__base_pose,
            baseOrientation=self.__base_orient,
            flags=flags_bullet,
            useFixedBase=self.__fixed_base,
        )
        self.__joint_id_for_link = {}
        self.__actuators_name_list = []

        _p_limits = [[], []]
        _v_limits = [[], []]
        _t_limits = [[], []]
        for _id in range(self.__p.getNumJoints(self.__robot_id)):
            # print(p.getJointInfo(self.__robot_id, _id))
            joint_info = self.__p.getJointInfo(self.__robot_id, _id)
            # print(joint_info)
            # print(self.__p.getBodyInfo(self.__robot_id))
            _name = joint_info[12].decode('UTF-8')
            if joint_info[4] != -1:
                self.__actuators_name_list.append(_name)
                _p_limits[0].append(joint_info[8]); _p_limits[1].append(joint_info[9])
                _t_limits[0].append(-joint_info[10]); _t_limits[1].append(joint_info[10])
                _v_limits[0].append(-joint_info[11]); _v_limits[1].append(joint_info[11])
            self.__joint_id_for_link[_name] = _id
        
        self.__actuators_id_list = [self.__joint_id_for_link[a] for a in self.__actuators_name_list]
        self.__num_actuators = len(self.__actuators_id_list)
        for i in range(0, 2):
            _p_limits[i] = np.array(_p_limits[i])
            _v_limits[i] = np.array(_v_limits[i])
            _t_limits[i] = np.array(_t_limits[i])
        # print(tuple(_p_limits))
        self.__joint_limits = robot.JointLimits(
            tuple(_p_limits),
            tuple(_v_limits),
            tuple(_t_limits)
        )
        self._joint_state = robot.JointState(self.__num_actuators)
        self.__p.setJointMotorControlArray(self.__robot_id, self.__actuators_id_list,
                                    self.__p.VELOCITY_CONTROL, 
                                    forces=np.zeros(self.__num_actuators))
        self.__initialized = True

        self._send_jointcontrol_torque(np.zeros(self.__num_actuators))
        
        # print("Num joints", self.__joint_id_for_link)

        for _id in range(self.__p.getNumJoints(self.__robot_id)):
            self.__p.enableJointForceTorqueSensor(self.__robot_id, _id, 1)
        
        self.__recalc_torque = np.zeros(self.__num_actuators)
        self._update_joint_state(self._joint_state)
        
    def clear_id(self):
       self.__initialized = False
       self.__robot_id = None

    @property
    def joint_controller_params(self) -> dict:
        return self.__joint_controller_params

    @property
    def robot_id(self) -> int:
        return self.__robot_id
    
    @property
    def joint_limits(self) -> robot.JointLimits:
        return self.__joint_limits
    
    @property
    def urdf_filename(self) -> str:
        return self._urdf_filename
    
    def link_id(self, link_name: str) -> int:
        return self.__joint_id_for_link[link_name]
    
    
    @joint_controller_params.setter
    def joint_controller_params(self, controller_params: dict):
        assert 'kp' in controller_params.keys() and 'kd' in controller_params.keys(), 'Dictionary does not contain kp or kd parameters'
        assert len(controller_params['kp']) == self.__num_actuators and len(controller_params['kd']) == self.__num_actuators, 'Shape of given parameters is not equal number of actuators'
        for k in controller_params.keys():
            self.__joint_controller_params[k] = controller_params[k]