from __future__ import annotations

import copy
from typing import Tuple
from abc import ABC, abstractmethod

import numpy as np
import scipy
from spatialmath import SE3, SO3

from itmobotics_sim.utils.robot import RobotControllerType, EEState, JointState, Robot, Motion


class VectorController(ABC):
    """_summary_"""

    def __init__(self):
        pass

    @abstractmethod
    def u(self, err: np.ndarray):
        pass

    @abstractmethod
    def reset(self):
        pass


class MPIDController(VectorController):
    """_summary_

    Args:
            P (np.ndarray): P coefficient
            I (np.ndarray): I coefficient
            D (np.ndarray): D coefficient
            dt (float): time step size
    """

    def __init__(self, P: np.ndarray, I: np.ndarray, D: np.ndarray, dt: float):
        super().__init__()
        self.__P = P
        self.__I = I
        self.__D = D
        self.__dt = dt
        self.__integral_value = np.zeros(self.__I.shape[0])
        self.__wind_up_max = np.ones(self.__I.shape[0]) * 0.1
        self.__last_err = np.zeros(self.__I.shape[0])

    def reset(self):
        """reset controller internal values"""
        self.__integral_value = np.zeros(self.__I.shape[0])
        self.__last_err = np.zeros(self.__I.shape[0])

    def u(self, err: np.ndarray) -> float:
        """calculate control

        Args:
            err (np.ndarray): error to aim

        Returns:
            float:
        """
        if err.shape[0] != self.__P.shape[0]:
            raise (RuntimeError("Invalid error shape"))
        nonlimit_integral = self.__I @ err * self.__dt + self.__integral_value
        abs_integral_value = np.minimum(self.__wind_up_max, np.abs(nonlimit_integral))
        self.__integral_value = np.multiply(abs_integral_value, np.sign(nonlimit_integral))
        # print(self.__integral_value)
        d_err = (err - self.__last_err) / (self.__dt + 1e-3)

        u = self.__P @ err + self.__integral_value + self.__D @ d_err
        self.__last_err = err
        # print(u)
        return u

    @property
    def P(self):
        return self.__P

    @property
    def I(self):
        return self.__I

    @property
    def D(self):
        return self.__D

    @P.setter
    def P(self, P: np.ndarray):
        assert (
            P.shape[0] == self.__P.shape[0] and P.shape[1] == self.__P.shape[1]
        ), "Invalid input matrix size, expected {:d}x{:d}, but given {:d}x{:d}".format(
            self.__P.shape[0], self.__P.shape[1], P.shape[0], P.shape[1]
        )
        self.__P = P

    @I.setter
    def I(self, I: np.ndarray):
        assert (
            I.shape[0] == self.__I.shape[0] and I.shape[1] == self.__I.shape[1]
        ), "Invalid input matrix size, expected {:d}x{:d}, but given {:d}x{:d}".format(
            self.__I.shape[0], self.__I.shape[1], I.shape[0], I.shape[1]
        )
        self.__I = I

    @D.setter
    def D(self, D: np.ndarray):
        assert (
            D.shape[0] == self.__D.shape[0] and D.shape[1] == self.__D.shape[1]
        ), "Invalid input matrix size, expected {:d}x{:d}, but given {:d}x{:d}".format(
            self.__D.shape[0], self.__D.shape[1], D.shape[0], D.shape[1]
        )
        self.__D = D


class ExternalController(ABC):
    """_summary_

    Args:
        rob (Robot): _description_
        robot_controller_type (str): _description_
    """

    def __init__(self, rob: Robot, robot_controller_type: str):
        self.robot = rob
        self.__robot_controller_type = robot_controller_type
        self.__child_controller = None

    def connect_controller(self, controller: ExternalController):
        """_summary_

        Args:
            controller (ExternalController): _description_
        """
        self.__child_controller = controller

    @abstractmethod
    def calc_control(self, target_motion: Motion) -> bool:
        pass

    def send_control_to_robot(self, target_motion: Motion) -> bool:
        """_summary_

        Args:
            target_motion (Motion): _description_

        Returns:
            bool: _description_
        """
        assert isinstance(target_motion, Motion), "Invalid type of target state, expected {:s}, but given {:s}".format(
            str(Motion), str(type(target_motion))
        )
        ok = self.calc_control(target_motion)
        if not ok:
            return False
        if not self.__child_controller is None:
            return self.__child_controller.send_control_to_robot(target_motion)
        return self.robot.set_control(target_motion, self.__robot_controller_type)


class SimpleController(ExternalController):
    """_summary_

    Args:
        ExternalController (_type_): _description_
    """

    def __init__(self, rob: Robot, robot_controller_type: str):
        super().__init__(rob, robot_controller_type)

    def calc_control(self, target_motion: Motion) -> bool:
        return True


class EEVelocityToJointVelocityController(ExternalController):
    """_summary_

    Args:
            robot (Robot): _description_
    """

    def __init__(self, robot: Robot):
        super().__init__(robot, RobotControllerType.JOINT_VELOCITIES)

    def calc_control(self, target_motion: Motion) -> bool:
        """_summary_

        Args:
            target_motion (Motion): _description_

        Returns:
            bool: _description_
        """
        target_motion.joint_state.joint_velocities = (
            np.linalg.pinv(
                self.robot.jacobian(
                    self.robot.joint_state.joint_positions,
                    target_motion.ee_state.ee_link,
                    target_motion.ee_state.ref_frame,
                )
            )
            @ target_motion.ee_state.twist
        )
        return True


class EELocalVelocityToJointVelocityController(ExternalController):
    """_summary_

    Args:
            robot (Robot): _description_
    """

    def __init__(self, robot: Robot):
        super().__init__(robot, RobotControllerType.JOINT_VELOCITIES)

    def calc_control(self, target_motion: Motion) -> bool:
        """_summary_

        Args:
            target_motion (Motion): _description_

        Returns:
            bool: _description_
        """
        target_motion.ee_state.twist = np.kron(np.eye(2),
            self.robot.ee_state(
                target_motion.ee_state.ee_link,
                target_motion.ee_state.ref_frame
            ).tf.R.T) @ target_motion.ee_state.twist

        target_motion.joint_state.joint_velocities = (
            np.linalg.pinv(
                self.robot.jacobian(
                    self.robot.joint_state.joint_positions,
                    target_motion.ee_state.ee_link,
                    target_motion.ee_state.ref_frame,
                )
            )
            @ target_motion.ee_state.twist
        )
        return True

class JointTorquesController(SimpleController):
    """_summary_

    Args:
        robot (Robot): _description_
    """

    def __init__(self, robot: Robot):
        super().__init__(robot, RobotControllerType.JOINT_TORQUES)


class JointPositionsController(SimpleController):
    """_summary_

    Args:
        robot (Robot): _description_
    """

    def __init__(self, robot: Robot):
        super().__init__(robot, RobotControllerType.JOINT_POSITIONS)


class JointVelocitiesController(SimpleController):
    """_summary_

    Args:
        robot (Robot): _description_
    """

    def __init__(self, robot: Robot):
        super().__init__(robot, RobotControllerType.JOINT_VELOCITIES)


class EEPositionToEEVelocityController(ExternalController):
    """_summary_

    Args:
        robot (Robot): _description_
    """

    def __init__(self, robot):
        super().__init__(robot, RobotControllerType.TWIST)
        self.__pid = MPIDController(10 * np.identity(6), 1e-4 * np.identity(6), 1e-1 * np.identity(6), 1e-3)

    def calc_control(self, target_motion: Motion) -> bool:
        assert isinstance(target_motion, Motion), "Invalid type of target state, expected {:s}, but given {:s}".format(
            str(Motion), str(type(target_motion))
        )

        current_state = self.robot.ee_state(target_motion.ee_state.ee_link)

        target_tf = target_motion.ee_state.tf
        current_tf = current_state.tf

        pose_err = target_tf.t - current_tf.t
        orient_error = target_tf.R @ current_tf.R.T
        twist_err = (SE3(*pose_err.tolist()) @ SE3(SO3(orient_error, check=False))).twist().A
        target_twist = self.__pid.u(twist_err)

        target_motion.ee_state.twist = target_twist
        return True


class EEForceHybrideToEEVelocityController(ExternalController):
    """_summary_

    Args:
            robot (Robot): _description_
            selected_axis (np.ndarray): _description_
            stiffnes (np.ndarray): _description_
            ref_basis (str, optional): _description_. Defaults to 'global'.
    """

    def __init__(self, robot: Robot, selected_axis: np.ndarray, stiffnes: np.ndarray, ref_basis: str = "global"):
        super().__init__(robot, RobotControllerType.TWIST)
        self.__pid = MPIDController(10 * np.identity(6), 1e-4 * np.identity(6), 1e-1 * np.identity(6), 1e-3)
        self.__ref_basis = ref_basis
        self.__stiffnes = stiffnes
        self.__T, self.__Y = EEForceHybrideToEEVelocityController.generate_square_selection_matrix(selected_axis)

    def calc_control(self, target_motion: Motion) -> bool:
        assert isinstance(target_motion, Motion), "Invalid type of target state, expected {:s}, but given {:s}".format(
            str(Motion), str(type(target_motion))
        )

        basis_frame = self.robot.ee_state(self.__ref_basis)
        control_basis = basis_frame.tf.R
        control_move_block = scipy.linalg.block_diag(control_basis, np.identity(3))
        control_force_block = scipy.linalg.block_diag(control_basis, control_basis)

        current_state = self.robot.ee_state(target_motion.ee_state.ee_link)

        target_tf = target_motion.ee_state.tf
        current_tf = current_state.tf

        pose_err = target_tf.t - control_basis.T @ current_tf.t
        orient_error = target_tf.R @ current_tf.R.T
        twist_err = (SE3(*pose_err.tolist()) @ SE3(SO3(orient_error, check=False))).twist().A

        force_torque_err = target_motion.ee_state.force_torque - control_move_block.T @ current_state.force_torque

        target_move_twist = control_move_block @ self.__T @ self.__pid.u(twist_err)
        target_force_torque_twist = control_force_block @ self.__Y @ self.__stiffnes @ -force_torque_err

        target_motion.ee_state.twist = target_move_twist + target_force_torque_twist
        return True

    def generate_square_selection_matrix(allow_moves: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """_summary_

        Args:
            allow_moves (np.ndarray): _description_

        Returns:
            Tuple[np.ndarray, np.ndarray]: _description_
        """
        T_matrix = np.diag(allow_moves)
        Y_matrix = np.identity(T_matrix.shape[0]) - T_matrix
        return (T_matrix, Y_matrix)
