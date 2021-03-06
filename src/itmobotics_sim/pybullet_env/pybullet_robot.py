from json import tool
from ntpath import join
import sys, os
import pybullet as p
from pybullet_utils import bullet_client as bc
from pybullet_utils import urdfEditor as ed
import numpy as np
import time
import uuid
import copy
from scipy.spatial.transform import Rotation as R
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import robot
from .urdf_editor import URDFEditor
from spatialmath import SE3,SO3
from spatialmath.base import r2q

class PyBulletRobot(robot.Robot):
    def __init__(self, urdf_filename: str, base_transform: SE3 = SE3()):
        super().__init__(urdf_filename, base_transform)
        self.__base_urdf_filename = urdf_filename
        self.__robot_id = None
        self.__initialized = False
        self.__actuators_name_list = []
        self.__actuators_id_list = []
        self.__num_actuators = 0
        self.__link_id = {}

        self.__external_models = {}
        self.__tool_list = []

    def __del__(self):
        for m in self.__external_models.keys():
            if os.path.exists(self.__external_models[m]["urdf_filename"]):
                os.remove(self.__external_models[m]["urdf_filename"])
    
    def connect_tool(self, tool_name: str, external_urdf_filename: str, root_link: str, tf: SE3 = SE3(), save = False):
        
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        
        main_editor = URDFEditor(self._urdf_filename)
        child_editor = URDFEditor(external_urdf_filename)

        main_editor.joinURDF(child_editor, root_link, tf.A)

        head = '/tmp'
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
    
    def remove_tool(self, tool_name):
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')

        for i in range(len(self.__tool_list)-1, -1, -1):
            last_tool = self.__tool_list[i]
            # del self.__external_models[last_tool]
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
    
    def reset_joint_state(self, jstate: robot.JointState):
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        self._joint_state = jstate
        for i in range(self.__num_actuators):
            p.resetJointState(self.__robot_id, self.__actuators_id_list[i], self._joint_state.joint_positions[i], self._joint_state.joint_velocities[i])
    
    def reset_ee_state(self, eestate: robot.EEState):
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        ref_frame = eestate.ref_frame
        base_ee_state = copy.deepcopy(eestate)
        if ref_frame!='world':
            refFrameState = p.getLinkState(self.__robot_id, self.__link_id[ref_frame])
            _,_,_,_, ref_frame_pos, ref_frame_rot = refFrameState
            in_base_tf =  SE3(*ref_frame_pos)@ SE3(SO3(R.from_quat(ref_frame_rot).as_matrix(), check=False))
            base_ee_state.tf = in_base_tf@base_ee_state.tf
            base_ee_state.twist = np.kron(np.eye(2,dtype=int), np.in_base_tf.R)@base_ee_state.twist

         # In PyBullet quaternioun described as xywz, but in spatialmath wxyz
        position = tuple(base_ee_state.tf.t)
        spm_quaternioun = tuple(r2q(base_ee_state.tf.R))
        orientation = tuple([spm_quaternioun[1], spm_quaternioun[2], spm_quaternioun[0], spm_quaternioun[3]])

        js = robot.JointState(self.__num_actuators)
        js.joint_positions = np.array(list(p.calculateInverseKinematics(self.__robot_id, self.__link_id[eestate.ee_link], position, orientation, maxNumIterations=1000, residualThreshold=1e-6)) )
        js.joint_velocities = np.linalg.pinv(self.jacobian(js.joint_positions, eestate.ee_link, eestate.ref_frame)) @ base_ee_state.twist
        # print(js)
        self.reset_joint_state(js)
        self._update_joint_state(js)


    def jacobian(self, joint_pose: np.ndarray, ee_link: str, ref_frame: str) -> np.ndarray:
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        Jv = np.zeros((3, len(joint_pose)))
        Jw = np.zeros((3, len(joint_pose)))

        if ee_link!='world':
            jac_t, jac_r = p.calculateJacobian(
                self.__robot_id, self.__link_id[ee_link], [0,0,0],
                list(joint_pose), list(np.zeros(joint_pose.shape)),
                list(np.zeros(joint_pose.shape))
            )
            Jv = np.asarray(jac_t)
            Jw = np.asarray(jac_r)
                
        if ref_frame!='world':
            # print("World")
            refFrameState = p.getLinkState(self.__robot_id, self.__link_id[ref_frame])
            _,_,_,_, ref_frame_pos, ref_frame_rot = refFrameState
            rot_matrix =  SO3(R.from_quat(ref_frame_rot).as_matrix(), check=False)
            Jv = rot_matrix.T @ np.asarray(Jv)
            Jw = rot_matrix.T @ np.asarray(Jw)

        J = np.concatenate((Jv,Jw), axis=0)
        return J
    
    def apply_force_sensor(self, link: str):
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
            
        self._force_sensor_link = link
        p.enableJointForceTorqueSensor(self.__robot_id, self.__link_id[link], 1)        
    
    def _send_cartcontrol_position(self, position: np.ndarray) -> bool:
        raise RuntimeError('Robot does not support this type of control')

    def _send_cartcontrol_velocity(self, velocity: np.ndarray) -> bool:
        raise RuntimeError('Robot does not support this type of control')

    def _send_jointcontrol_velocity(self, velocity: np.ndarray) -> bool:
        if not self.__initialized:
            return False
        p.setJointMotorControlArray(self.__robot_id,
            self.__actuators_id_list,
            p.VELOCITY_CONTROL,
            targetVelocities=velocity.tolist(),
            forces = self.__joint_controller_params['max_torque'].tolist()
        )
        return True
    
    def _send_jointcontrol_position(self, position: np.ndarray) -> bool:
        if not self.__initialized:
            return False
        p.setJointMotorControlArray(self.__robot_id,
            self.__actuators_id_list,
            p.POSITION_CONTROL,
            targetPositions=position.tolist(),
            targetVelocities=np.zeros(self.__num_actuators).tolist(),
            positionGains=self.__joint_controller_params['kp'].tolist(),
            velocityGains=self.__joint_controller_params['kd'].tolist()
        )
        return True
    
    def _send_jointcontrol_torque(self, torque: np.ndarray) -> bool:
        if not self.__initialized:
            return False
        p.setJointMotorControlArray(self.__robot_id, self.__actuators_id_list,
            p.VELOCITY_CONTROL, 
            forces=np.zeros(self.__num_actuators))
        p.setJointMotorControlArray(self.__robot_id, 
            self.__actuators_id_list,
            controlMode = p.TORQUE_CONTROL, 
            forces = torque.tolist()
        )
        return True
    
    def _update_cartesian_state(self, tool_state: robot.EEState):
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        if tool_state.ee_link!='world':
            eeState = p.getLinkState(self.__robot_id, self.__link_id[tool_state.ee_link])
            _,_,_,_, link_frame_pos, link_frame_rot = eeState
        else:
            link_frame_pos = np.zeros(3)
            link_frame_rot = np.array([0,0,0,1])

        tool_state.tf = SE3(*link_frame_pos) @ SE3(SO3(R.from_quat(link_frame_rot).as_matrix(), check=False))

        if tool_state.ref_frame !='world':
            refFrameState = p.getLinkState(self.__robot_id, self.__link_id[tool_state.ref_frame])
            _,_,_,_, ref_frame_pos, ref_frame_rot = refFrameState
            tool_state.tf = (SE3(*ref_frame_pos) @ SE3(SO3(R.from_quat(ref_frame_rot).as_matrix(), check=False))).inv() @ tool_state.tf 

        self._update_joint_state(self.joint_state)
        tool_state.twist = self.jacobian(self.joint_state.joint_positions, tool_state.ee_link, tool_state.ref_frame).dot(
            self.joint_state.joint_velocities
        )
        
        if not self._force_sensor_link is None:
            pb_joint_state = p.getJointStates(self.__robot_id, [self.__link_id[self._force_sensor_link]])
            tool_state.force_torque = np.array(pb_joint_state[0][2])
    
    def _update_joint_state(self, joint_state: robot.JointState):
        if not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        pb_joint_state = p.getJointStates(self.__robot_id, self.__actuators_id_list)
        joint_state.joint_positions = np.array([state[0] for state in pb_joint_state])
        joint_state.joint_velocities = np.array([state[1] for state in pb_joint_state])
        joint_state.joint_torques = np.array([state[3] for state in pb_joint_state])
    
    def remove_robot_body(self):
        if self.__robot_id is None:
            return
        elif not self.__initialized:
            raise RuntimeError('Robot was not initialized')
        else:
            p.removeBody(self.__robot_id)
            self.__robot_id = None

    def reset(self):
        self.remove_robot_body()
        self._urdf_filename = self.__base_urdf_filename
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

        self.__base_pose = self._base_transform.t.tolist() # World position [x,y,z]
        self.__base_orient = R.from_matrix(self._base_transform.R).as_quat().tolist() # Quaternioun [x,y,z,w]
        # print("Loading urdf ", self._urdf_filename)
        self.__robot_id = p.loadURDF(
            self._urdf_filename,
            basePosition=self.__base_pose,
            baseOrientation=self.__base_orient,
            useFixedBase=True,
            flags=p.URDF_USE_SELF_COLLISION
        )
        self.__num_joints = p.getNumJoints(self.__robot_id)
        self.__link_id = {}
        self.__actuators_name_list = []

        for _id in range(self.__num_joints):
            # print(p.getJointInfo(self.__robot_id, _id))
            _name = p.getJointInfo(self.__robot_id, _id)[12].decode('UTF-8')
            if p.getJointInfo(self.__robot_id, _id)[4] != -1:
                self.__actuators_name_list.append(_name)
            self.__link_id[_name] = _id
        
        self.__actuators_id_list = [self.__link_id[a] for a in self.__actuators_name_list]
        self.__num_actuators = len(self.__actuators_id_list)
        self._joint_state = robot.JointState(self.__num_actuators)
        p.setJointMotorControlArray(self.__robot_id, self.__actuators_id_list,
                                    p.VELOCITY_CONTROL, 
                                    forces=np.zeros(self.__num_actuators))
        self.__initialized = True

        self._send_jointcontrol_torque(np.zeros(self.__num_actuators))
        # self._force_sensor_link = None
        self.__joint_controller_params = {
            'kp': np.ones(self.__num_actuators),
            'kd': np.ones(self.__num_actuators),
            'max_torque': 100*np.ones(self.__num_actuators)
        }
        # print("Num joints", self.__link_id)

    def clear_id(self):
       self.__initialized = False
       self.__robot_id = None


    @property
    def joint_controller_params(self) -> dict:
        return self.__joint_controller_params

    @property
    def robot_id(self) -> int:
        return self.__robot_id
    
    
    @joint_controller_params.setter
    def joint_controller_params(self, controller_params: dict):
        assert 'kp' in controller_params.keys() and 'kd' in controller_params.keys(), 'Dictionary does not contain kp or kd parameters'
        assert len(controller_params['kp']) == self.__num_actuators and len(controller_params['kd']) == self.__num_actuators, 'Shape of given parameters is not equal number of actuators'
        self.__joint_controller_params = controller_params
