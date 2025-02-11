import re
import os
import math
import numpy as np
from shapely.geometry import Point, Polygon, MultiPoint, LineString
from shapely.ops import unary_union
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # For 3D plotting
from matplotlib.widgets import Slider
import trimesh

# --- FUNCTIONS TO LOAD MODIFIER GEOMETRY ---

def load_modifier_polygon_from_stl_2d(stl_filename):
    try:
        mesh = trimesh.load(stl_filename)
    except Exception as e:
        print(f"Error loading STL file '{stl_filename}': {e}")
        raise e
    points_2d = [(v[0], v[1]) for v in mesh.vertices]
    polygon = MultiPoint(points_2d).convex_hull
    return polygon

def load_modifier_mesh_3d(stl_filename):
    try:
        mesh = trimesh.load(stl_filename)
    except Exception as e:
        print(f"Error loading STL file '{stl_filename}': {e}")
        raise e
    vertices = np.array(mesh.vertices)
    faces = np.array(mesh.faces) if hasattr(mesh, "faces") else None
    cx = float(np.mean(vertices[:, 0]))
    cy = float(np.mean(vertices[:, 1]))
    cz = float(np.mean(vertices[:, 2]))
    centroid = (cx, cy, cz)
    distances = np.linalg.norm(vertices - np.array(centroid), axis=1)
    r_max = float(np.max(distances))
    return {"mesh": mesh, "vertices": vertices, "faces": faces, "centroid": centroid, "r_max": r_max}

# --- CONFIGURATION ---
pattern_x = re.compile(r'X([-+]?[0-9]*\.?[0-9]+)')
pattern_y = re.compile(r'Y([-+]?[0-9]*\.?[0-9]+)')
pattern_z = re.compile(r'Z([-+]?[0-9]*\.?[0-9]+)')
pattern_e = re.compile(r'E([-+]?[0-9]*\.?[0-9]+)')

input_file = "input.gcode"
output_file = "output.gcode"

# --- MODIFIER DEFINITIONS ---
# Set "modifier_type" to either "2D" or "3D"
modifier_defs = [
    {"filename": "modifier1.stl", "modifier_type": "2D", "center_multiplier": 1.5, "edge_multiplier": 1.5, "gradient_exponent": 1.0, "min_layer": 0.0},
    {"filename": "modifier2.stl", "modifier_type": "3D", "center_multiplier": 2,   "edge_multiplier": 2,   "gradient_exponent": 2,   "min_layer": 0.0}
]

# --- LOAD MODIFIERS ---
modifiers = []
for mod_def in modifier_defs:
    mfile = mod_def["filename"]
    mod_type = mod_def.get("modifier_type", "2D")
    if os.path.exists(mfile):
        if mod_type == "2D":
            polygon = load_modifier_polygon_from_stl_2d(mfile)
            centroid = polygon.centroid
            r_max = max(centroid.distance(Point(v)) for v in polygon.exterior.coords)
            mod_params = {
                "modifier_type": "2D",
                "polygon": polygon,
                "centroid_2d": centroid,
                "r_max": r_max,
                "center_multiplier": mod_def["center_multiplier"],
                "edge_multiplier": mod_def["edge_multiplier"],
                "gradient_exponent": mod_def.get("gradient_exponent", 1.0),
                "min_layer": mod_def.get("min_layer", 0.0)
            }
        elif mod_type == "3D":
            mesh_info = load_modifier_mesh_3d(mfile)
            mod_params = {
                "modifier_type": "3D",
                "mesh": mesh_info["mesh"],
                "vertices": mesh_info["vertices"],
                "faces": mesh_info["faces"],
                "centroid_3d": mesh_info["centroid"],
                "r_max": mesh_info["r_max"],
                "center_multiplier": mod_def["center_multiplier"],
                "edge_multiplier": mod_def["edge_multiplier"],
                "gradient_exponent": mod_def.get("gradient_exponent", 1.0),
                "min_layer": mod_def.get("min_layer", 0.0)
            }
        else:
            print(f"Unknown modifier type for file {mfile}; skipping.")
            continue
        modifiers.append(mod_params)
        print(f"Loaded modifier from '{mfile}' as type {mod_type}:")
        if mod_type == "2D":
            print(mod_params["polygon"])
            print(f"Centroid (2D): ({mod_params['centroid_2d'].x:.2f}, {mod_params['centroid_2d'].y:.2f}), r_max: {mod_params['r_max']:.2f}")
        elif mod_type == "3D":
            cx, cy, cz = mod_params["centroid_3d"]
            print(f"Centroid (3D): ({cx:.2f}, {cy:.2f}, {cz:.2f}), r_max: {mod_params['r_max']:.2f}")
        print(f"Center multiplier: {mod_params['center_multiplier']}, Edge multiplier: {mod_params['edge_multiplier']}, Exponent: {mod_params['gradient_exponent']}, min_layer: {mod_params['min_layer']}")
    else:
        print(f"Modifier STL file '{mfile}' not found. Skipping.")

# --- FUNCTION TO COMPUTE MULTIPLIER FOR A SINGLE MODIFIER ---
def compute_multiplier_for_modifier(x, y, z, mod):
    if z < mod["min_layer"]:
        return 1.0
    if mod["modifier_type"] == "2D":
        pt2d = Point(x, y)
        if not mod["polygon"].contains(pt2d):
            return 1.0
        r = pt2d.distance(mod["centroid_2d"])
        norm_base = mod["r_max"]
    elif mod["modifier_type"] == "3D":
        cx, cy, cz = mod["centroid_3d"]
        # Use full 3D distance:
        r3d = math.sqrt((x - cx)**2 + (y - cy)**2 + (z - cz)**2)
        if r3d > mod["r_max"]:
            return 1.0
        r = r3d
        norm_base = mod["r_max"]
    else:
        return 1.0
    normalized = min(r / norm_base, 1.0) ** mod["gradient_exponent"]
    mod_multiplier = mod["center_multiplier"] - (mod["center_multiplier"] - mod["edge_multiplier"]) * normalized
    return mod_multiplier

# --- MULTIPLE MODIFIER GRADIENT FUNCTION ---
def compute_multiplier_multiple(x, y, z):
    overall = 1.0
    for mod in modifiers:
        m = compute_multiplier_for_modifier(x, y, z, mod)
        overall *= m
    return overall

# --- FUNCTION TO COMPUTE AVERAGE MULTIPLIER ALONG A MOVE ---
def compute_average_multiplier(start, end, z, num_samples=5):
    line = LineString([start, end])
    total = 0.0
    for i in range(num_samples):
        frac = (i + 0.5) / num_samples
        pt = line.interpolate(frac, normalized=True)
        total += compute_multiplier_multiple(pt.x, pt.y, z)
    return total / num_samples

# --- FUNCTION TO COMPUTE EFFECTIVE MULTIPLIER FOR A MOVE ---
def compute_effective_multiplier(start, end, z, num_samples=5):
    line = LineString([start, end])
    applicable_polys = [mod["polygon"] for mod in modifiers if mod.get("modifier_type", "2D")=="2D" and z >= mod["min_layer"]]
    if applicable_polys:
        union_poly = unary_union(applicable_polys)
        start_inside = union_poly.contains(Point(start.x, start.y))
        end_inside = union_poly.contains(Point(end.x, end.y))
    else:
        start_inside = end_inside = True
    if start_inside == end_inside:
        return compute_average_multiplier(start, end, z, num_samples=num_samples)
    else:
        return 1.0

# --- PROCESSING THE G-CODE (INTEGRATED APPROACH) ---
positions_3d = []  
new_E = 0.0
last_E = 0.0
last_x, last_y, last_z = None, None, 0

with open(input_file, "r") as fin, open(output_file, "w") as fout:
    for line in fin:
        if line.startswith("G92") and "E" in line:
            match = pattern_e.search(line)
            if match:
                reset_val = float(match.group(1))
                last_E = reset_val
                new_E = reset_val
                print(f"Reset extrusion with G92: setting E to {reset_val}")
            fout.write(line)
            continue
        z_match = pattern_z.search(line)
        if z_match:
            last_z = float(z_match.group(1))
        if line.startswith("G1") and "E" in line:
            x_match = pattern_x.search(line)
            y_match = pattern_y.search(line)
            e_match = pattern_e.search(line)
            if e_match:
                current_E = float(e_match.group(1))
                delta_E = current_E - last_E
                last_E = current_E
                if x_match and y_match:
                    x = float(x_match.group(1))
                    y = float(y_match.group(1))
                    start_pt = Point(last_x, last_y) if (last_x is not None and last_y is not None) else Point(x, y)
                    last_x, last_y = x, y
                elif last_x is not None and last_y is not None:
                    x, y = last_x, last_y
                    start_pt = Point(last_x, last_y)
                else:
                    x, y = 0, 0
                    start_pt = Point(0, 0)
                end_pt = Point(x, y)
                z = last_z  
                total_length = LineString([start_pt, end_pt]).length
                if total_length < 1e-6:
                    m_val = compute_multiplier_multiple(x, y, z)
                    seg_delta = delta_E * m_val
                    new_E += seg_delta
                    new_line = re.sub(r'E[-+]?[0-9]*\.?[0-9]+', f"E{new_E:.5f}", line)
                    fout.write(new_line)
                    positions_3d.append((x, y, z, m_val))
                else:
                    effective_m = compute_effective_multiplier(start_pt, end_pt, z, num_samples=5)
                    new_delta = delta_E * effective_m
                    new_E += new_delta
                    new_line = re.sub(r'E[-+]?[0-9]*\.?[0-9]+', f"E{new_E:.5f}", line)
                    fout.write(new_line)
                    positions_3d.append((end_pt.x, end_pt.y, z, effective_m))
            else:
                fout.write(line)
        else:
            fout.write(line)

if not positions_3d:
    print("No extrusion moves found in the input G-code.")
else:
    xs_all = [pt[0] for pt in positions_3d]
    ys_all = [pt[1] for pt in positions_3d]
    zs_all = [pt[2] for pt in positions_3d]
    print(f"G-code X range: {min(xs_all):.2f} to {max(xs_all):.2f}")
    print(f"G-code Y range: {min(ys_all):.2f} to {max(ys_all):.2f}")
    print(f"G-code Z range: {min(zs_all):.2f} to {max(zs_all):.2f}")

# --- DOWNSAMPLING FOR VISUALIZATION ---
max_points = 10000  
if len(positions_3d) > max_points:
    sample_rate = int(len(positions_3d) / max_points)
    positions_sampled = positions_3d[::sample_rate]
    print(f"Downsampling: using every {sample_rate}th point for preview.")
else:
    positions_sampled = positions_3d

xs = np.array([pt[0] for pt in positions_sampled])
ys = np.array([pt[1] for pt in positions_sampled])
zs = np.array([pt[2] for pt in positions_sampled])
multipliers = np.array([pt[3] for pt in positions_sampled])

# --- 3D VISUALIZATION WITH SLIDER ---

def set_axes_equal(ax):
    x_limits = ax.get_xlim3d()
    y_limits = ax.get_ylim3d()
    z_limits = ax.get_zlim3d()
    x_range = abs(x_limits[1] - x_limits[0])
    y_range = abs(y_limits[1] - y_limits[0])
    z_range = abs(z_limits[1] - z_limits[0])
    plot_radius = 0.5 * max(x_range, y_range, z_range)
    x_middle = np.mean(x_limits)
    y_middle = np.mean(y_limits)
    z_middle = np.mean(z_limits)
    ax.set_xlim3d([x_middle - plot_radius, x_middle + plot_radius])
    ax.set_ylim3d([y_middle - plot_radius, y_middle + plot_radius])
    ax.set_zlim3d([z_middle - plot_radius, z_middle + plot_radius])

fig = plt.figure(figsize=(10, 8))
ax = fig.add_subplot(111, projection='3d')
plt.subplots_adjust(bottom=0.25)
scatter = ax.scatter(xs, ys, zs, c=multipliers, cmap='viridis')
cbar = plt.colorbar(scatter, ax=ax, pad=0.1)
cbar.set_label("Extrusion Multiplier")
ax.set_xlabel("X")
ax.set_ylabel("Y")
ax.set_zlabel("Z (Layer Height)")
ax.set_title("3D Visualization of Extrusion Multipliers by Layer")

# Plot modifiers.
z_ref = min(zs)
for mod in modifiers:
    if mod.get("modifier_type", "2D") == "2D":
        x_poly, y_poly = mod["polygon"].exterior.xy
        ax.plot(x_poly, y_poly, zs=z_ref, zdir="z", color="red", linewidth=2, label="2D Modifier")
    elif mod.get("modifier_type") == "3D":
        cx, cy, cz = mod["centroid_3d"]
        # Compute effective XY circle at reference Z.
        if z_ref < cz - mod["r_max"] or z_ref > cz + mod["r_max"]:
            continue
        r_eff = math.sqrt(max(0, mod["r_max"]**2 - (z_ref - cz)**2))
        theta = np.linspace(0, 2*math.pi, 100)
        x_circle = cx + r_eff * np.cos(theta)
        y_circle = cy + r_eff * np.sin(theta)
        ax.plot(x_circle, y_circle, zs=z_ref, zdir="z", color="blue", linewidth=2, label="3D Modifier")
ax.legend()
set_axes_equal(ax)

slider_ax = plt.axes([0.25, 0.1, 0.65, 0.03])
z_min = min(zs)
z_max = max(zs)
slider = Slider(slider_ax, "Max Z", z_min, z_max, valinit=z_max)

def update(val):
    threshold = slider.val
    mask = zs <= threshold
    new_xs = xs[mask]
    new_ys = ys[mask]
    new_zs = zs[mask]
    new_multipliers = multipliers[mask]
    scatter._offsets3d = (new_xs, new_ys, new_zs)
    scatter.set_array(new_multipliers)
    fig.canvas.draw_idle()

slider.on_changed(update)
plt.show()
