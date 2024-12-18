# Merged Camera Calibration Script (Intrinsic and Extrinsic) - With Direct Pairwise Overlap Assertions

# Approach is fixed to specific camera positioning such that all cameras can see the pattern at the same time
# Also it ignores frames where a camera does not detect the pattern for simplicity

# TODO: Calibrate using global optimization (for now stereoCalibration is used that utilizes only information of each camera with the pivot camera, but with using global optimization we can utilize the data between other pairs to improve accuracy and consistency among the cameras between each other)

import sys
import cv2
import numpy as np
import json5
import argparse

# Set up argument parser to accept various parameters
parser = argparse.ArgumentParser(description="Camera Calibration Script")
parser.add_argument(
    "--file",
    type=str,
    default="setup.json5",
    help="Path to the state declarations file",
)
parser.add_argument(
    "--n",
    type=int,
    default=12,
    help="Number of calibration images required per camera",
)
parser.add_argument(
    "--chessboard_size",
    type=str,
    default="9x13",
    help="Chessboard size as columns x rows (inner corners), e.g., '9x6'",
)
parser.add_argument(
    "--square_size",
    type=float,
    default=13,
    help="Size of a square in millimeters",
)
parser.add_argument(
    "-f",
    "--force",
    help="Force overwrite calibrations",
    action="store_true",
)
parser.add_argument(
    "--window_scale",
    type=float,
    default=0.7,
    help="Scale of a window",
)
parser.add_argument(
    "--use_existing_intrinsics",
    help="Use existing intrinsics, don't overwrite them",
    action="store_true",
)
parser.add_argument(
    "--pivot",
    type=int,
    default=None,
    help="Index of the pivot (reference) camera",
)

args = parser.parse_args()
cameras_path = args.file
calibration_images_needed = args.n

# Parse chessboard size argument
try:
    chessboard_cols, chessboard_rows = map(int, args.chessboard_size.lower().split("x"))
    chessboard_size = (chessboard_cols, chessboard_rows)
except ValueError:
    print("Error: Invalid chessboard_size format. Use 'colsxrows', e.g., '9x6'.")
    sys.exit(1)

square_size = args.square_size

# Load camera configurations from the JSON file
with open(cameras_path, "r") as f:
    cameras_confs = json5.load(f)

# Notify if calibration already exists
if not args.force and any(
    "extrinsic" in cam or (("intrinsic" in cam) and not args.use_existing_intrinsics)
    for cam in cameras_confs
):
    print("Some calibration already exists. Use --force to overwrite.", file=sys.stderr)
    sys.exit(1)

# Initialize video captures
print("\nLaunching...")
cameras = {}
for camera_conf in cameras_confs:
    idx = camera_conf["index"]
    cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        print(f"Error: Could not open camera {idx}", file=sys.stderr)
        sys.exit(1)

    # Disable autofocus
    autofocus_supported = cap.get(cv2.CAP_PROP_AUTOFOCUS) != -1
    if autofocus_supported:
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)

    # Set manual focus value
    focus_value = camera_conf.get("focus", 0)
    focus_supported = cap.set(cv2.CAP_PROP_FOCUS, focus_value)
    if not focus_supported:
        print(
            f"Camera {idx} does not support manual focus! (or invalid focus value provided)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Initialize camera data
    cameras[idx] = {
        "index": idx,
        "cap": cap,
        "image_size": None,
        "corners": None,
    }

# Collect camera indices
camera_indices = [camera_conf["index"] for camera_conf in cameras_confs]

# Validate pivot camera index
if args.pivot is not None:
    if args.pivot not in camera_indices:
        print(
            f"Error: Specified pivot camera index {args.pivot} is not in the list of available cameras."
        )
        sys.exit(1)
    reference_idx = args.pivot
else:
    # Default to the first camera index
    reference_idx = camera_indices[0]

print(f"\nUsing camera {reference_idx} as the pivot (reference) camera.")

print()
print("=== Camera Calibration Script ===")
print("Instructions:")
print(
    f"1. Ensure that the calibration pattern ({chessboard_size[0]}x{chessboard_size[1]} chessboard of {square_size}mm squares) is visible in all cameras you want to calibrate."
)
print("2. Press 'c' to capture calibration images.")
print("   The script will print which cameras detected the pattern.")
print("3. Press 's' to perform calibration when ready.")
print(
    f"   Calibration requires at least {calibration_images_needed} captures when all cameras detect the pattern."
    f"   Captures when a camera does not detect the pattern will be skipped."
)
print(
    "4. After calibration, the script will write the intrinsic and extrinsic parameters back to the cameras file."
)
print()

# Set up windows for each camera feed
for idx in cameras:
    cv2.namedWindow(f"Camera_{idx}", cv2.WINDOW_NORMAL)

shots = []

# Capture images
while True:
    # Read frames from all cameras
    for cam in cameras.values():
        cap = cam["cap"]
        ret, frame = cap.read()
        if not ret:
            print(f"Error: Could not read from camera {idx}")
            continue

        cam["frame"] = frame

    # Display and detect corners
    for idx in cameras:
        cam = cameras[idx]

        frame = cam["frame"]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        ret_corners, corners, meta = cv2.findChessboardCornersSBWithMeta(
            gray,
            chessboard_size,
            flags=(
                cv2.CALIB_CB_MARKER
                | cv2.CALIB_CB_EXHAUSTIVE
                | cv2.CALIB_CB_ACCURACY
                | cv2.CALIB_CB_NORMALIZE_IMAGE
            ),
        )

        if ret_corners:
            if meta.shape[0] != chessboard_rows:
                corners = corners.reshape(-1, 2)
                corners = corners.reshape(*chessboard_size, 2)
                corners = corners.transpose(1, 0, 2)
                corners = corners.reshape(-1, 2)
                corners = corners[:, np.newaxis, :]
            cv2.drawChessboardCorners(frame, chessboard_size, corners, ret_corners)
            cam["corners"] = corners
        else:
            cam["corners"] = None

        if cam["image_size"] is None:
            cam["image_size"] = gray.shape[::-1]
        else:
            assert cam["image_size"] == gray.shape[::-1]

        # Resize the frame before displaying
        frame_height, frame_width = frame.shape[:2]
        new_width = int(frame_width * args.window_scale)
        new_height = int(frame_height * args.window_scale)
        resized_frame = cv2.resize(
            frame, (new_width, new_height), interpolation=cv2.INTER_AREA
        )

        # Display the resized frame
        cv2.imshow(f"Camera_{idx}", resized_frame)

    key = cv2.waitKey(1)
    if key & 0xFF == ord("q"):
        print("Exiting calibration script.")
        sys.exit()

    elif key & 0xFF == ord("c"):
        # Verify shot
        missing_cameras = [
            idx for idx in camera_indices if cameras[idx]["corners"] is None
        ]
        if missing_cameras:
            print("Not all cameras have detected the pattern.")
            print(f"+- Cameras missing pattern: {sorted(missing_cameras)}")
            continue

        # Collect detected corners
        shots.append({idx: cameras[idx]["corners"] for idx in camera_indices})

        # Print how many shots remains
        print(f"Captured {len(shots)}/{calibration_images_needed}.")

    elif key & 0xFF == ord("s"):
        # Check if can proceed
        if len(shots) < calibration_images_needed:
            remaining_shots = calibration_images_needed - len(shots)
            print(
                f"Not enough shots collected. Please capture {remaining_shots} more shots."
            )
            continue

        print("\nProceeding to calibration...")
        break

# Release resources after loop
for idx in cameras:
    cameras[idx]["cap"].release()
cv2.destroyAllWindows()

# Prepare object points based on the real-world dimensions of the calibration pattern
objp = np.zeros((chessboard_size[1] * chessboard_size[0], 3), np.float32)
objp[:, :2] = np.mgrid[0 : chessboard_size[0], 0 : chessboard_size[1]].T.reshape(-1, 2)
objp *= square_size

# Perform intrinsic calibrations
for idx in cameras:
    cam = cameras[idx]
    cam_conf = next(conf for conf in cameras_confs if conf["index"] == idx)

    if args.use_existing_intrinsics and "intrinsic" in cam_conf:
        print(f"Using existing intrinsic parameters for camera {idx}.")
        # Reconstruct camera matrix and distortion coefficients
        intrinsic_conf = cam_conf["intrinsic"]
        fx = intrinsic_conf["focal_length_pixels"][0]
        fy = intrinsic_conf["focal_length_pixels"][1]
        s = intrinsic_conf["skew_coefficient"]
        cx = intrinsic_conf["principal_point"][0]
        cy = intrinsic_conf["principal_point"][1]
        mtx = np.array([[fx, s, cx], [0, fy, cy], [0, 0, 1]])
        dist_coeffs = np.array(intrinsic_conf["dist_coeffs"])
    else:
        print(f"Performing intrinsic calibration for camera {idx}...")
        ret, mtx, dist_coeffs, _, _ = cv2.calibrateCamera(
            [objp for _ in range(len(shots))],
            [shot[idx] for shot in shots],
            cam["image_size"],
            None,
            None,
        )
        dist_coeffs = dist_coeffs.flatten()

        # Extract intrinsic parameters
        fx, fy = mtx[0, 0], mtx[1, 1]
        s, cx, cy = mtx[0, 1], mtx[0, 2], mtx[1, 2]

        # Store intrinsic parameters
        cam_conf["intrinsic"] = {
            "focal_length_pixels": [fx, fy],
            "skew_coefficient": s,
            "principal_point": [cx, cy],
            "dist_coeffs": dist_coeffs.tolist(),
        }

    # Store intrinsic parameters for later use
    cam["mtx"] = mtx
    cam["dist_coeffs"] = dist_coeffs

print("\nComputing transformations relative to the pivot camera...")

# Pivot camera extrinsic is identity
pivot_cam_conf = next(conf for conf in cameras_confs if conf["index"] == reference_idx)
pivot_cam_conf["extrinsic"] = {
    "translation_mm": [0, 0, 0],
    "rotation_rodrigues": [0, 0, 0],
    "rotation_matrix": [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
    ],
}

# Prepare shared data before transoformation compute
objpoints = [objp for _ in range(len(shots))]

imgpoints1 = [shot[reference_idx] for shot in shots]

mtx1 = cameras[reference_idx]["mtx"]
dist1 = cameras[reference_idx]["dist_coeffs"]

image_size = cameras[reference_idx]["image_size"]

# Compute transformations for each camera relative to the pivot
for idx in camera_indices:
    if idx == reference_idx:
        continue

    imgpoints2 = [shot[idx] for shot in shots]

    mtx2 = cameras[idx]["mtx"]
    dist2 = cameras[idx]["dist_coeffs"]

    # Stereo calibration
    ret, _, _, _, _, R, T, E, F = cv2.stereoCalibrate(
        objpoints,
        imgpoints1,
        imgpoints2,
        mtx1,
        dist1,
        mtx2,
        dist2,
        image_size,
        criteria=(
            cv2.TERM_CRITERIA_MAX_ITER + cv2.TERM_CRITERIA_EPS,
            100,
            1e-5,
        ),
        flags=cv2.CALIB_FIX_INTRINSIC,
    )

    cam_conf = next(conf for conf in cameras_confs if conf["index"] == idx)
    cam_conf["extrinsic"] = {
        "translation_mm": T.flatten().tolist(),
        "rotation_rodrigues": cv2.Rodrigues(R)[0].flatten().tolist(),
        # "rotation_matrix": R.tolist(),
    }

    print(f"Computed transformation from camera {reference_idx} to camera {idx}.")

# Save calibrations
with open(cameras_path, "w") as f:
    json5.dump(cameras_confs, f, indent=4)
print("\nCameras file updated with intrinsic and extrinsic parameters.")
