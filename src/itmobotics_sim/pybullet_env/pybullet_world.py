import pybullet as p
import pybullet_data
import pybullet_utils.bullet_client as bc
import numpy as np
import time
import enum
from spatialmath import SE3, SO3
from scipy.spatial.transform import Rotation as R
from .pybullet_robot import PyBulletRobot

class GUI_MODE(enum.Enum):
    DIRECT = enum.auto()
    SIMPLE_GUI = enum.auto()
    

class PyBulletWorld():
    def __init__(self, urdf_filename: str ='plane.urdf', gui_mode: GUI_MODE = GUI_MODE.SIMPLE_GUI, time_step:float = 1e-3, time_scale:float = 1):
        self.__urdf_filename = urdf_filename
        self.__time_step = time_step
        self.__time_scale = max(time_scale, 1.0)
        assert self.__time_scale < 1e3, "Large time scale doesn't support, please chose less than 1e3"
        assert self.__time_step < 1.0, "Large time step doesn't support, please chose less than 1.0 sec"
        self.__robots = {}

        self.__pybullet_gui_mode = p.DIRECT
        
        if gui_mode == GUI_MODE.SIMPLE_GUI:
            self.__pybullet_gui_mode = p.GUI
        self.__client = p.connect(self.__pybullet_gui_mode)
        
        p.setAdditionalSearchPath(pybullet_data.getDataPath())

        self.__objects = {}
        self.reset()
    
    def add_robot(self, robot: PyBulletRobot, name: str = 'robot') -> bool:
        if name in self.__robots.keys():
            return False
        self.__robots[name] = robot
        robot.reset()
        return True
    
    def add_object(self, name:str, urdf_filename: str, base_transform: SE3 = SE3(), fixed: bool = True, save: bool = False, scale_size: float = 1.0):
        if name in self.__objects.keys():
            self.remove_object(name)
            print('Replace object with name {:s}'.format(name))
        self.__append_object(name, urdf_filename, base_transform, fixed, save, scale_size)
    
    def __append_object(self, name:str, urdf_filename: str, base_transform: SE3, fixed: bool, save: bool, scale_size: float):
        base_pose = base_transform.t.tolist() # World position [x,y,z]
        base_orient = R.from_matrix(base_transform.R).as_quat().tolist() # Quaternioun [x,y,z,w]
        obj_id = p.loadURDF(
            urdf_filename,
            basePosition=base_pose,
            baseOrientation=base_orient,
            useFixedBase=fixed,
            globalScaling=scale_size
        )
        print("Load {:s}, in {:s}, given id: {:s}".format( name, str(base_pose), str(obj_id)))
        num_joints = p.getNumJoints(obj_id)
        print("NUm joints: ", num_joints)
        link_id = {}
        for _id in range(num_joints):
            _name = p.getJointInfo(obj_id, _id)[12].decode('UTF-8')
            link_id[_name] = _id
        
        self.__objects[name] = {"id": obj_id, "urdf_filename": urdf_filename, "base_tf": base_transform, "fixed": fixed, "save": save, "link_id": link_id, "scale_size": scale_size}
    
    def remove_object(self, name: str):
        assert name in self.__objects, "Undefined object: {:s}".format(name)
        p.removeBody(self.__objects[name]["id"])
        del self.__objects[name]

    def link_tf(self, model_name: str, link: str, reference_model_name: str, reference_link: str) -> SE3:
        if model_name in self.__objects:
            pr = p.getLinkState(self.__objects[model_name]["id"], self.__objects[model_name]["link_id"][link])
        elif model_name in self.__robots:
            pr = p.getLinkState(self.__robots[model_name].robot_id, self.__robots[model_name].link_id(link))
        else:
            raise RuntimeError('Unknown model name. Please check that object or robot model has been added to the simulator with name: {:s}.\n List of added robot models: {:s}.\n List of added object models: {:s}'.format(model_name, str(list(self.__robots.keys())), str(list(self.__objects.keys()))))
        _,_,_,_, link_frame_pos, link_frame_rot = pr
        tf_link_in_to_world = SE3(*link_frame_pos) @ SE3(SO3(R.from_quat(link_frame_rot).as_matrix(), check=False))
        print(tf_link_in_to_world)
        if reference_model_name == "" or reference_link == "world":
            return tf_link_in_to_world

        # print(self.__robots)
        # print(reference_model_name)
        if reference_model_name in self.__objects:
            pr = p.getLinkState(self.__objects[reference_model_name]["id"], self.__objects[reference_model_name]["link_id"][reference_link])
        elif reference_model_name in self.__robots:
            pr = p.getLinkState(self.__robots[reference_model_name].robot_id, self.__robots[reference_model_name].link_id(reference_link))
        else:
            raise RuntimeError('Unknown reference model name. Please check that object or robot model has been added to the simulator with name: {:s}.\n List of added robot models: {:s}.\n List of added object models: {:s}'.format(reference_model_name, str(list(self.__robots.keys())), str(list(self.__objects.keys()))))
        _,_,_,_, link_frame_pos, link_frame_rot = pr
        reference_tf_link_in_to_world = SE3(*link_frame_pos) @ SE3(SO3(R.from_quat(link_frame_rot).as_matrix(), check=False))
        print(reference_tf_link_in_to_world)

        tf_in_to_reference = reference_tf_link_in_to_world.inv() @ tf_link_in_to_world
        print(tf_in_to_reference)
        return tf_in_to_reference
    

    def sim_step(self):
        p.stepSimulation()
        self.__sim_time = time.time()-self.__start_sim_time
        time.sleep(self.__time_step/self.__time_scale)
    
    
    def reset(self):
        for r in self.__robots.keys():
            self.__robots[r].clear_id()

        p.resetSimulation()
        p.setGravity(0, 0, -9.82)
        p.setTimeStep(self.__time_step)
        p.setPhysicsEngineParameter(fixedTimeStep=self.__time_step, numSolverIterations=100, numSubSteps=2, useSplitImpulse=1)
        p.setRealTimeSimulation(False)

        self.__world_model = p.loadURDF(self.__urdf_filename, useFixedBase=True)
        
        for r in self.__robots.keys():
            self.__robots[r].reset()

        self.__sim_time = 0.0
        self.__start_sim_time = time.time()
        for n in self.__objects:
            obj = dict(self.__objects[n])
            if obj["save"]:
                self.__append_object(n, obj["urdf_filename"], obj["base_tf"], obj["fixed"], obj["save"], obj["scale_size"])
        
        # For the initial state calculation of the models

    @property
    def sim_time(self) -> float:
        return self.__sim_time

    @property
    def client(self) -> float:
        return self.__client
