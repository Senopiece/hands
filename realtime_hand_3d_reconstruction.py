import cv2
import mediapipe as mp
import numpy as np
import json
import argparse
import sys
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


def load_camera_parameters(cameras_file):
    with open(cameras_file, "r") as f:
        cameras_confs = json.load(f)

    cameras = {}
    for cam_conf in cameras_confs:
        idx = cam_conf["index"]
        if "intrinsic" not in cam_conf or "extrinsic" not in cam_conf:
            print(f"Camera {idx} does not have necessary calibration data.")
            continue
        # Intrinsic parameters
        intrinsic = cam_conf["intrinsic"]
        mtx = np.array(
            [
                [
                    intrinsic["focal_length_pixels"]["x"],
                    intrinsic["skew_coefficient"],
                    intrinsic["principal_point"]["x"],
                ],
                [
                    0,
                    intrinsic["focal_length_pixels"]["y"],
                    intrinsic["principal_point"]["y"],
                ],
                [0, 0, 1],
            ]
        )
        dist_coeffs = np.array(intrinsic["dist_coeffs"])
        # Extrinsic parameters
        extrinsic = cam_conf["extrinsic"]
        T_cm = extrinsic["translation_centimeters"]
        R_rad = extrinsic["rotation_radians"]
        # Convert rotation from Euler angles to rotation matrix
        yaw = R_rad["yaw"]
        pitch = R_rad["pitch"]
        roll = R_rad["roll"]
        R = euler_angles_to_rotation_matrix(yaw, pitch, roll)
        T = (
            np.array([[T_cm["x"]], [T_cm["y"]], [T_cm["z"]]]) * 10
        )  # Convert to millimeters
        # Store parameters
        cameras[idx] = {
            "mtx": mtx,
            "dist": dist_coeffs,
            "R": R,
            "T": T,
            "cap": None,
            "frame": None,
            "hand_landmarks": None,
            "hands_tracker": None,  # To be initialized later
            "focus": cam_conf.get("focus", 0),  # Retrieve focus value
        }
    return cameras


def euler_angles_to_rotation_matrix(yaw, pitch, roll):
    """
    Converts Euler angles (in radians) to a rotation matrix.
    """
    R_z = np.array(
        [[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]]
    )
    R_y = np.array(
        [
            [np.cos(pitch), 0, np.sin(pitch)],
            [0, 1, 0],
            [-np.sin(pitch), 0, np.cos(pitch)],
        ]
    )
    R_x = np.array(
        [[1, 0, 0], [0, np.cos(roll), -np.sin(roll)], [0, np.sin(roll), np.cos(roll)]]
    )
    R = R_z @ R_y @ R_x
    return R


def triangulate_points(cameras, point_idx):
    """
    Triangulate 3D point from multiple camera views.
    """
    projections = []
    points = []
    for idx in cameras:
        cam = cameras[idx]
        if cam["hand_landmarks"] is not None:
            lm = cam["hand_landmarks"][point_idx]
            # Convert normalized coordinates to pixel coordinates
            h, w, _ = cam["frame"].shape
            x = lm.x * w
            y = lm.y * h
            # Undistort points
            undistorted = cv2.undistortPoints(
                np.array([[[x, y]]], dtype=np.float32),
                cam["mtx"],
                cam["dist"],
                P=cam["mtx"],
            )
            points.append(undistorted[0][0])
            # Compute projection matrix
            RT = np.hstack((cam["R"], cam["T"]))
            P = cam["mtx"] @ RT
            projections.append(P)
    if len(projections) >= 2:
        # Prepare matrices for triangulation
        A = []
        for i in range(len(projections)):
            P = projections[i]
            x, y = points[i]
            A.append(x * P[2, :] - P[0, :])
            A.append(y * P[2, :] - P[1, :])
        A = np.array(A)
        # Solve using SVD
        U, S, Vt = np.linalg.svd(A)
        X = Vt[-1]
        X /= X[3]
        return X[:3]
    else:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="3D Hand Reconstruction using MediaPipe and Multiple Cameras"
    )
    parser.add_argument(
        "--file",
        type=str,
        default="setup.json",
        help="Path to the cameras declarations file",
    )
    args = parser.parse_args()
    cameras_path = args.file

    # Load camera parameters
    cameras = load_camera_parameters(cameras_path)
    if len(cameras) < 2:
        print("Need at least two cameras with calibration data.")
        sys.exit(1)

    # Initialize video captures and MediaPipe Hands trackers
    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    for idx in cameras:
        # Initialize video capture
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            print(f"Error: Could not open camera {idx}")
            sys.exit(1)
        # Disable autofocus
        autofocus_supported = cap.get(cv2.CAP_PROP_AUTOFOCUS) != -1
        if autofocus_supported:
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        # Set manual focus value
        focus_value = cameras[idx]["focus"]
        focus_supported = cap.set(cv2.CAP_PROP_FOCUS, focus_value)
        if not focus_supported:
            print(
                f"Camera {idx} does not support manual focus! (or an invalid focus value provided)",
                file=sys.stderr,
            )
            sys.exit(1)
        cameras[idx]["cap"] = cap
        cv2.namedWindow(f"Camera_{idx}", cv2.WINDOW_NORMAL)
        # Initialize MediaPipe Hands tracker for each camera
        cameras[idx]["hands_tracker"] = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.9,
            min_tracking_confidence=0.9,
        )

    # Start capturing frames
    while True:
        for idx in cameras:
            cap = cameras[idx]["cap"]
            ret, frame = cap.read()
            if not ret:
                print(f"Error: Could not read from camera {idx}")
                continue
            cameras[idx]["frame"] = frame
            # Process frame with MediaPipe
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hands = cameras[idx]["hands_tracker"]
            results = hands.process(rgb_frame)
            cameras[idx]["hand_landmarks"] = None
            if results.multi_hand_landmarks:
                for hand_landmarks, handedness in zip(
                    results.multi_hand_landmarks, results.multi_handedness
                ):
                    if handedness.classification[0].label == "Right":
                        cameras[idx]["hand_landmarks"] = hand_landmarks.landmark
                        # Draw landmarks using the provided code
                        mp_drawing.draw_landmarks(
                            frame,
                            hand_landmarks,
                            mp_hands.HAND_CONNECTIONS,
                            mp_drawing.DrawingSpec(
                                color=(0, 0, 255), thickness=2, circle_radius=4
                            ),
                            mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2),
                        )
                        break  # Only consider one right hand
            cv2.imshow(f"Camera_{idx}", frame)

        key = cv2.waitKey(1)
        if key & 0xFF == ord("q"):
            break
        elif key & 0xFF == ord("s"):
            # Collect landmarks from all cameras
            points_3d = []
            num_landmarks = 21  # MediaPipe Hands has 21 landmarks
            valid_indices = []
            for point_idx in range(num_landmarks):
                point_3d = triangulate_points(cameras, point_idx)
                if point_3d is not None:
                    points_3d.append(point_3d)
                    valid_indices.append(point_idx)
            if points_3d:
                points_3d = np.array(points_3d)
                # Visualize the 3D hand
                fig = plt.figure()
                ax = fig.add_subplot(111, projection="3d")
                # Plot the landmarks
                xs = points_3d[:, 0]
                ys = points_3d[:, 1]
                zs = points_3d[:, 2]
                ax.scatter(xs, ys, zs, c="r", marker="o")
                # Use mp_hands.HAND_CONNECTIONS for connections
                for connection in mp_hands.HAND_CONNECTIONS:
                    i, j = connection
                    # Check if both landmarks were reconstructed
                    if i in valid_indices and j in valid_indices:
                        idx_i = valid_indices.index(i)
                        idx_j = valid_indices.index(j)
                        ax.plot(
                            [points_3d[idx_i, 0], points_3d[idx_j, 0]],
                            [points_3d[idx_i, 1], points_3d[idx_j, 1]],
                            [points_3d[idx_i, 2], points_3d[idx_j, 2]],
                            "b",
                        )
                # Set labels
                ax.set_xlabel("X")
                ax.set_ylabel("Y")
                ax.set_zlabel("Z")
                ax.set_title("3D Reconstructed Right Hand")
                # Adjust the view angle for better visualization
                ax.view_init(elev=20, azim=-60)
                plt.show()
            else:
                print("Not enough data to reconstruct hand in 3D.")

    # Release resources
    for idx in cameras:
        cameras[idx]["cap"].release()
        cameras[idx]["hands_tracker"].close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
