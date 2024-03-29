import numpy as np

def intrinsic2GLprojection_matrix(intrinsic_matrix: np.ndarray, resolution: np.ndarray, clip: np.ndarray) -> np.ndarray:
    """intrinsic to GL projection matrix
    
    Convert intrinsic_matrix to GL projectionMatrix

    Args:
        intrinsic_matrix (np.ndarray): (3,3) intrinsic camera matrix [[f_x, 0, c_x], [0, f_y,  c_y], [0, 0, 1]]
        resolution (np.ndarray): (2,) resolution of the image [width, height]
        clip (np.ndarray): (2,) clip image parameter for camera range [near, far]
    
    Returns:
        np.array: (16,) inline projection matrix as used in openGL and pybullet
    """ 
    near, far = clip
    w, h = resolution
    f_x = intrinsic_matrix[0,0]
    f_y = intrinsic_matrix[1,1]
    c_x = intrinsic_matrix[0,2]
    c_y = intrinsic_matrix[1,2]
    A = (near + far)/(near - far)
    B = 2 * near * far / (near - far)

    projection_matrix = [
                        [2/w * f_x,  0,          (w - 2*c_x)/w,  0],
                        [0,          2/h * f_y,  (2*c_y - h)/h,  0],
                        [0,          0,          A,              B],
                        [0,          0,          -1,             0]]
    #The transpose is needed for respecting the array structure of the OpenGL
    return np.array(projection_matrix).T.reshape(16).tolist()


def extrinsic2GLview_matrix(extrinsic_matrix: np.ndarray) -> np.ndarray:
    """extrinsic to GL view matrix

    Args:
        extrinsic_matrix (np.ndarray): (4,4) extrinsic camera matrix

    Returns:
        np.ndarray: (16,) inline view matrix as used in openGL and pybullet
    """
    # Convert opencv convention to python convention
    # By a 180 degrees rotation along X
    Tc = np.array([[1,   0,    0,  0],
                    [0,  -1,    0,  0],
                    [0,   0,   -1,  0],
                    [0,   0,    0,  1]]).reshape(4,4)
    
    # pybullet pse is the inverse of the pose from the ROS-TF
    T=Tc@np.linalg.inv(extrinsic_matrix)
    # The transpose is needed for respecting the array structure of the OpenGL
    viewMatrix = T.T.reshape(16)
    return viewMatrix