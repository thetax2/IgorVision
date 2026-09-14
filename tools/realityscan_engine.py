"""
tools/realityscan_engine.py
===========================
Qt-free engine for the RealityScan **command centre**.

The pipeline is modelled as an ordered list of *command nodes* (each node
is one RealityScan CLI command plus its parameters) plus a set of free
*variables* (``%NAME%``).  Execution order is the **left-to-right** order
of the nodes (their ``x`` position), which matches RealityScan's
sequential CLI and the ComfyUI/Blender "flow left → right" mental model.

This module is deliberately free of Qt so it can be unit-tested headless.
It provides:

* :data:`COMMANDS` – a registry of CLI commands with parameter specs.
* :class:`RSNode` / :class:`RSPipeline` – the pipeline data model.
* :func:`resolve` – ``%VAR%`` substitution.
* :func:`build_args` – flat CLI argument list (for direct execution).
* :func:`build_batch` – a portable ``.bat`` artifact.
* :func:`to_json` / :func:`from_json` – pipeline (de)serialisation.
* :func:`from_batch` – import an existing ``.bat`` into a pipeline.
* :data:`PRESETS` – ready-made pipelines (incl. "HighDetail RAW + Distances").
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# command registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    """One parameter of a CLI command."""
    name: str
    required: bool = False
    kind: str = "text"          # text | path | number | bool | choice
    choices: tuple = ()
    description: str = ""


@dataclass(frozen=True)
class CommandSpec:
    """A CLI command and its parameter contract."""
    name: str
    category: str
    params: tuple = ()          # tuple[ParamSpec]
    produces: str = ""          # semantic output: model | component | ortho | …
    consumes: str = ""          # semantic input
    description: str = ""


COMMANDS: dict[str, CommandSpec] = {}


def _p(name, required=False, kind="text", choices=(), description=""):
    return ParamSpec(name, required, kind, tuple(choices), description)


def _c(name, category, params=(), produces="", consumes="", description=""):
    COMMANDS[name] = CommandSpec(
        name, category, tuple(params), produces, consumes, description
    )


# ── Project ─────────────────────────────────────────────────────────
_c("newScene", "Project", description="Create a new empty scene.")
_c("save", "Project", (_p("path", False, "path", (), "Project file (.rcproj)"),),
   description="Save the current project (or to a path).")
_c("load", "Project",
   (_p("project", True, "path"),
    _p("autosave", False, "choice", ("recoverAutosave", "deleteAutosave"))),
   description="Load an existing project.")
_c("addFolder", "Project", (_p("folder", True, "path"),),
   consumes="images", description="Add all images in a folder.")
_c("add", "Project", (_p("images", True, "path"),),
   consumes="images", description="Import images or an .imagelist.")
_c("addImageWithCalibration", "Project",
   (_p("image", True, "path"), _p("xmp", True, "path")),
   consumes="images", description="Import image + XMP calibration.")
_c("importVideo", "Project",
   (_p("video", True, "path"), _p("frames", True, "path"), _p("jumps", True, "number")),
   consumes="images", description="Import extracted video frames.")
_c("importLaserScan", "Project",
   (_p("scan", True, "path"), _p("params", False, "path")),
   consumes="images", description="Add a LiDAR scan.")
_c("importHDRimages", "Project",
   (_p("source", True, "path"), _p("params", False, "path")),
   consumes="images", description="Import HDR / 16-bit images.")
_c("importColmap", "Project",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Import a COLMAP project.")
_c("importBundler", "Project",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Import a Bundler project.")
_c("importCache", "Project", (_p("folder", True, "path"),),
   description="Import resource cache data.")
_c("clearCache", "Project", description="Clear the application cache.")
_c("importGlobalSettings", "Project", (_p("file", True, "path"),),
   description="Import global settings (.rcconfig).")
_c("exportGlobalSettings", "Project", (_p("file", True, "path"),),
   description="Export global settings (.rcconfig).")
_c("execRSCMD", "Project", (_p("file", True, "path"),),
   description="Execute commands from an .rscmd file.")
_c("unlockPPIProject", "Project", (_p("project", True, "path"),),
   description="Unlock a PPI project.")

# ── Image selection / per-image settings ─────────────────────────────
_c("selectImage", "Images",
   (_p("image", True, "path"),
    _p("op", False, "choice", ("union", "sub", "intersect", "toggle"))),
   description="Select image(s) by path or regex.")
_c("selectAllImages", "Images", description="Select all images.")
_c("deselectAllImages", "Images", description="Deselect all images.")
_c("invertImageSelection", "Images", description="Invert image selection.")
_c("enableAlignment", "Images", (_p("value", True, "bool"),),
   description="Enable/disable images in registration.")
_c("enableMeshing", "Images", (_p("value", True, "bool"),),
   description="Enable/disable images in meshing.")
_c("enableTexturingAndColoring", "Images", (_p("value", True, "bool"),),
   description="Enable/disable images in texturing.")
_c("setWeightInTexturing", "Images", (_p("weight", True, "number"),),
   description="Set texturing weight (0..1).")
_c("setFeatureSource", "Images",
   (_p("mode", True, "choice", ("0", "1", "2")),),
   description="Feature source: 0 merge, 1 component, 2 all.")
_c("setCalibrationGroupByExif", "Images", description="Group inputs by Exif.")
_c("setConstantCalibrationGroups", "Images", description="Group inputs into one group.")
_c("lockPoseForContinue", "Images", (_p("value", True, "bool"),),
   description="Lock relative pose for next registration.")
_c("setPriorCalibrationGroup", "Images", (_p("number", True, "number"),),
   description="Prior calibration group (-1 = none).")
_c("setPriorLensGroup", "Images", (_p("number", True, "number"),),
   description="Prior lens group (-1 = none).")
_c("enableColorNormalization", "Images", (_p("value", True, "bool"),),
   description="Enable/disable color normalization.")
_c("enableColorNormalizationReference", "Images", (_p("value", True, "bool"),),
   description="Set images as color references.")
_c("setDownscaleForDepthMaps", "Images", (_p("factor", True, "number"),),
   description="Downscale factor for depth maps.")
_c("enableInComponent", "Images", (_p("value", True, "bool"),),
   description="Enable images in meshing and continue.")
_c("generateAIMasks", "Images", description="AI-mask the object of interest.")
_c("exportMasks", "Images", (_p("folder", True, "path"), _p("params", False, "path")),
   description="Export mask images.")
_c("setImageLayer", "Images",
   (_p("index", True, "number"), _p("path", True, "path"), _p("type", True, "text")),
   description="Set a layer on one image.")
_c("setImagesLayer", "Images",
   (_p("path", True, "path"), _p("type", True, "text")),
   description="Set a layer on selected images.")
_c("removeImageLayer", "Images", (_p("type", True, "text"),),
   description="Remove a layer type from selected images.")

# ── Alignment ────────────────────────────────────────────────────────
_c("align", "Alignment", produces="component", description="Align images.")
_c("draft", "Alignment", produces="component", description="Align in draft mode.")
_c("update", "Alignment", description="Update components/models to fit constraints.")
_c("detectFeatures", "Alignment", description="Run feature detection.")
_c("mergeComponents", "Alignment", description="Merge created components.")
_c("selectComponent", "Alignment", (_p("name", True, "text"),), consumes="component",
   description="Select a component by name.")
_c("selectMaximalComponent", "Alignment", consumes="component",
   description="Select the largest component.")
_c("selectComponentWithLeastReprojectionError", "Alignment", consumes="component",
   description="Select component with least reprojection error.")
_c("renameSelectedComponent", "Alignment", (_p("name", True, "text"),),
   description="Rename the selected component.")
_c("deleteSelectedComponent", "Alignment", description="Delete the selected component.")
_c("deleteComponent", "Alignment", (_p("index", True, "number"),),
   description="Delete a component by index (0-based).")
_c("deleteAllComponents", "Alignment", description="Delete all components.")
_c("importComponent", "Alignment", (_p("file", True, "path"),),
   description="Import a component (.rsalign).")
_c("exportLatestComponents", "Alignment", (_p("folder", True, "path"),),
   produces="component", description="Export latest components (.rsalign).")
_c("exportSelectedComponentDir", "Alignment", (_p("folder", True, "path"),),
   description="Export selected component to a folder.")
_c("exportSelectedComponentFile", "Alignment", (_p("file", True, "path"),),
   description="Export selected component to a file.")
_c("setMinComponentSize", "Alignment", (_p("size", True, "number"),),
   description="Minimal component size for export.")
_c("exportXMP", "Alignment", (_p("params", False, "path"),),
   description="Export camera metadata (XMP).")
_c("exportXMPForSelectedComponent", "Alignment", description="Export XMP for selected component.")
_c("exportRegistration", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export registration.")
_c("exportUndistortedImages", "Alignment",
   (_p("folder", True, "path"), _p("params", False, "path")),
   description="Export undistorted images.")
_c("exportSTMap", "Alignment",
   (_p("folder", False, "path"), _p("params", False, "path")),
   description="Export ST maps.")
_c("exportSparsePointCloud", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   produces="pointcloud", description="Export sparse point cloud.")
_c("importFlightLog", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Import a trajectory file.")
_c("importGroundControlPoints", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Import ground control points.")
_c("exportGroundControlPoints", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export ground control points.")
_c("importControlPointsMeasurements", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Import control-point measurements.")
_c("exportControlPointsMeasurements", "Alignment",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export control-point measurements.")
_c("listControlPoints", "Alignment", (_p("file", True, "path"),),
   description="Export a list of control points.")
_c("selectControlPoint", "Alignment", (_p("name", True, "text"),),
   description="Select a control point by name.")
_c("invertControlPointSelection", "Alignment", description="Invert CP selection.")
_c("renameControlPoint", "Alignment",
   (_p("name", True, "text"), _p("newName", True, "text")),
   description="Rename a control point.")
_c("renameSelectedControlPoint", "Alignment", (_p("newName", True, "text"),),
   description="Rename the selected control point.")
_c("deleteControlPoint", "Alignment", (_p("index", False, "number"),),
   description="Delete selected control point.")
_c("selectMeasurementByError", "Alignment",
   (_p("error", True, "number"), _p("cp", False, "text")),
   description="Select measurements with error >= value.")
_c("selectMeasurementByIndex", "Alignment",
   (_p("cp", True, "text"), _p("index", True, "number")),
   description="Select a CP measurement by index.")
_c("deleteControlPointMeasurement", "Alignment", description="Remove selected CP measurements.")
_c("defineDistance", "Alignment",
   (_p("distance", True, "path"), _p("params", False, "path")),
   description="Define/import a distance constraint.")
_c("editConstraintSelection", "Alignment", (_p("kv", True, "text"),),
   description="Edit selected constraints (key=value).")
_c("deleteConstraint", "Alignment", (_p("index", False, "number"),),
   description="Remove selected distance constraints.")
_c("detectMarkers", "Alignment", (_p("params", False, "path"),),
   description="Detect markers in images.")
_c("setCamerasGravityDirection", "Alignment", (_p("componentID", False, "text"),),
   description="Align component to gravity (from XMP).")

# ── Reconstruction ───────────────────────────────────────────────────
_c("resetGround", "Reconstruction", description="Reset the ground plane.")
_c("setGroundPlaneFromReconstructionRegion", "Reconstruction",
   description="Center a model using a reconstruction region.")
_c("setReconstructionRegionAuto", "Reconstruction", description="Auto reconstruction region.")
_c("setReconstructionRegion", "Reconstruction", (_p("box", True, "path"),),
   description="Import a reconstruction region (.rsbox).")
_c("setReconstructionRegionOnCPs", "Reconstruction",
   (_p("cp1", True, "text"), _p("cp2", True, "text"), _p("cp3", True, "text"),
    _p("height", False, "number")),
   description="Set region on control points.")
_c("setReconstructionRegionByDensity", "Reconstruction",
   description="Set region to densest part.")
_c("scaleReconstructionRegion", "Reconstruction",
   (_p("x", True, "number"), _p("y", True, "number"), _p("z", True, "number"),
    _p("mode", False, "choice", ("absolute", "factor")),
    _p("origin", False, "choice", ("center", "origin"))),
   description="Scale the reconstruction region.")
_c("moveReconstructionRegion", "Reconstruction",
   (_p("x", True, "number"), _p("y", True, "number"), _p("z", True, "number")),
   description="Move the reconstruction region.")
_c("rotateReconstructionRegion", "Reconstruction",
   (_p("x", True, "number"), _p("y", True, "number"), _p("z", True, "number")),
   description="Rotate the reconstruction region (degrees).")
_c("offsetReconstructionRegion", "Reconstruction",
   (_p("x", True, "number"), _p("y", True, "number"), _p("z", True, "number")),
   description="Offset the reconstruction region.")
_c("exportReconstructionRegion", "Reconstruction", (_p("box", True, "path"),),
   description="Export the reconstruction region (.rsbox).")
_c("calculatePreviewModel", "Reconstruction", produces="model",
   description="Calculate a preview-quality mesh.")
_c("calculateNormalModel", "Reconstruction", produces="model",
   description="Calculate a normal-quality mesh.")
_c("calculateHighModel", "Reconstruction", produces="model",
   description="Calculate the highest-quality mesh.")
_c("continueModelCalculation", "Reconstruction", description="Continue a paused/crashed calc.")

# ── Model tools ──────────────────────────────────────────────────────
_c("selectModel", "Model", (_p("name", True, "text"),), consumes="model",
   description="Select a model by name.")
_c("deleteSelectedModel", "Model", description="Delete the selected model.")
_c("duplicateSelectedModel", "Model", produces="model", description="Duplicate the selected model.")
_c("renameSelectedModel", "Model", (_p("name", True, "text"),),
   description="Rename the selected model.")
_c("correctColors", "Model", (_p("layer", False, "text"),),
   description="Run color correction.")
_c("unwrap", "Model", (_p("params", False, "path"),), description="Calculate the unwrap.")
_c("calculateTexture", "Model", (_p("params", False, "path"),),
   description="Calculate texture.")
_c("calculateQualityTexture", "Model", description="Calculate quality texture.")
_c("reprojectTexture", "Model",
   (_p("source", True, "text"), _p("result", True, "text"), _p("params", False, "path")),
   description="Reproject texture between models.")
_c("calculateVertexColors", "Model", description="Calculate coloring.")
_c("calculatePreviewVertexColors", "Model", description="Calculate draft coloring.")
_c("calculateQualityColors", "Model", description="Calculate quality vertex colors.")
_c("simplify", "Model", (_p("target", False, "number"), _p("params", False, "path")),
   description="Simplify the model.")
_c("smooth", "Model", (_p("params", False, "path"),), description="Smooth the model.")
_c("closeHoles", "Model", (_p("maxEdges", False, "number"),), description="Close model holes.")
_c("cleanModel", "Model", description="Clean the model (non-manifold, small holes).")
_c("selectTrianglesInsideReconReg", "Model", description="Select triangles inside region.")
_c("selectTrianglesOutsideReconReg", "Model", description="Select triangles outside region.")
_c("selectMarginalTriangles", "Model", description="Select marginal triangles.")
_c("selectLargeTrianglesAbs", "Model", (_p("threshold", True, "number"),),
   description="Select triangles with edge > threshold.")
_c("selectLargeTrianglesRel", "Model", (_p("threshold", True, "number"),),
   description="Select triangles with edge > threshold * avg.")
_c("selectLargestModelComponent", "Model", description="Select largest connected component.")
_c("invertTrianglesSelection", "Model", description="Invert triangle selection.")
_c("deselectModelTriangles", "Model", description="Deselect all triangles.")
_c("removeSelectedTriangles", "Model", produces="model",
   description="Create a new model without the selected triangles.")
_c("cutByBox", "Model",
   (_p("side", True, "choice", ("inner", "outer")), _p("fillHoles", False, "bool")),
   produces="model", description="Filter triangles inside/outside the region.")
_c("undercut", "Model", description="Undercut the model per cluster box.")
_c("exportModel", "Model",
   (_p("name", True, "text"), _p("file", True, "path"), _p("params", False, "path")),
   consumes="model", description="Export a model to a file.")
_c("exportSelectedModel", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   consumes="model", description="Export the selected model.")
_c("exportModelToZip", "Model",
   (_p("path", True, "path"), _p("format", False, "choice", (".obj", ".fbx"))),
   description="Export the model to a zip archive.")
_c("importModel", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   produces="model", description="Import a model from a file.")
_c("calculateOrthoProjection", "Model",
   (_p("rsortho", False, "path"), _p("rsbox", False, "path")),
   produces="ortho", description="Calculate an orthographic projection.")
_c("selectOrthoProjection", "Model", (_p("name", True, "text"),), consumes="ortho",
   description="Select an ortho projection by name.")
_c("exportOrthoProjection", "Model",
   (_p("name", True, "text"), _p("path", True, "path"), _p("params", False, "path")),
   consumes="ortho", description="Export an ortho projection.")
_c("calculateCrossSections", "Model", (_p("step", False, "number"), _p("axis", False, "number")),
   description="Calculate cross sections.")
_c("exportCrossSections", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export cross sections.")
_c("computeContours", "Model", (_p("params", False, "path"),), description="Compute contours.")
_c("exportContours", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export contours.")
_c("exportShapes", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export shapes (.json).")
_c("exportReport", "Model",
   (_p("output", True, "path"), _p("template", True, "path"), _p("withReports", False, "bool")),
   description="Export a report (.html).")
_c("generateMaskFromMesh", "Model", description="Generate masks from camera views + model.")
_c("exportMapsAndMask", "Model",
   (_p("folder", False, "path"), _p("params", False, "path")),
   description="Export masks + depth/normal maps.")
_c("exportLod", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export a linear LoD model.")
_c("export3dTiles", "Model",
   (_p("file", True, "path"), _p("params", False, "path")),
   description="Export Cesium 3D Tiles (.json).")
_c("exportCameraSnapshots", "Model",
   (_p("folder", True, "path"), _p("params", False, "path")),
   description="Render images from camera positions.")
_c("uploadToSketchfab", "Model", (_p("token", True, "text"),),
   description="Upload the model to Sketchfab.")

# ── Classification ───────────────────────────────────────────────────
_c("dtmClassify", "Classification", (_p("params", False, "path"),),
   description="Classify vertices into DTM classes.")
_c("selectClassification", "Classification", (_p("name", True, "text"),),
   description="Select a classification by name.")
_c("renameSelectedClassification", "Classification", (_p("name", True, "text"),),
   description="Rename the selected classification.")
_c("transferClassification", "Classification", (_p("params", False, "path"),),
   description="Transfer classification from label layers.")
_c("exportClassificationSettings", "Classification", (_p("file", True, "path"),),
   description="Export AI-Classify settings.")
_c("importClassificationSettings", "Classification", (_p("file", True, "path"),),
   description="Import AI-Classify settings.")
_c("selectClass", "Classification", (_p("name", True, "text"),),
   description="Select a class by name.")
_c("deselectClass", "Classification", description="Deselect all classes.")
_c("renameSelectedClass", "Classification", (_p("name", True, "text"),),
   description="Rename the selected class.")
_c("setSelectedClassAsGroundForDTM", "Classification", (_p("value", True, "bool"),),
   description="Use class as ground for DTM.")
_c("setSelectedClassLasFormat", "Classification", (_p("value", True, "number"),),
   description="Set LAS export class (0-12).")
_c("selectVerticesOfSelectedClass", "Classification", description="Select vertices of a class.")
_c("colorModelBySelectedClassification", "Classification",
   description="Colorize the model by classes.")
_c("deleteSelectedClassification", "Classification", description="Delete the selected classification.")

# ── Settings / error handling ────────────────────────────────────────
_c("set", "Settings", (_p("kv", True, "text"),),
   description="Change an application setting (key=value).")
_c("preset", "Settings", (_p("kv", True, "text"),),
   description="Change a setting during setup phase.")
_c("reset", "Settings",
   (_p("scope", True, "choice", ("ui", "cfg", "cfgui", "all")),),
   description="Reset UI / settings / both / clean install.")
_c("silent", "Settings", (_p("crashReportPath", False, "path"),),
   description="Suppress warning dialogs + crash upload.")
_c("writeProgress", "Settings",
   (_p("file", True, "path"), _p("timeout", False, "number")),
   description="Write progress changes to a file.")
_c("printProgress", "Settings", (_p("timeout", False, "number"),),
   description="Print progress to the console.")
_c("tag", "Settings", description="Write a tag to the console.")
_c("stdConsole", "Settings", description="Redirect console to stdout.")
_c("disableOnlineCommunication", "Settings", description="Disable online communication.")
_c("setProjectCoordinateSystem", "Settings", (_p("crs", True, "text"),),
   description="Set project coordinate system (authority:id).")
_c("setOutputCoordinateSystem", "Settings", (_p("crs", True, "text"),),
   description="Set output coordinate system (authority:id).")
_c("headless", "Settings", description="Hide the UI (must be at startup).")
_c("hideUI", "Settings", description="Hide the UI (any time).")
_c("showUI", "Settings", description="Show the hidden UI.")
_c("quit", "Settings", description="Quit the application.")


def spec_for(command: str) -> Optional[CommandSpec]:
    return COMMANDS.get(command)


def categories() -> list[str]:
    seen: list[str] = []
    for c in COMMANDS.values():
        if c.category not in seen:
            seen.append(c.category)
    return seen


# ---------------------------------------------------------------------------
# pipeline model
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class RSNode:
    """One CLI command node in the pipeline."""
    command: str
    params: dict = field(default_factory=dict)   # ordered name → value
    x: float = 0.0
    y: float = 0.0
    enabled: bool = True
    id: str = field(default_factory=_new_id)


@dataclass
class RSPipeline:
    """A full RealityScan pipeline."""
    name: str = "Pipeline"
    exe: str = r"C:\Program Files\Epic Games\RealityScan_2.1\RealityCapture.exe"
    mode: str = "gui"          # gui | headless
    quit: bool = False
    variables: dict = field(default_factory=dict)
    nodes: list = field(default_factory=list)     # list[RSNode]
    connections: list = field(default_factory=list)  # list[(src_id, dst_id)]


# ---------------------------------------------------------------------------
# variable resolution
# ---------------------------------------------------------------------------

VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def resolve(text, variables: dict) -> str:
    """Substitute ``%NAME%`` tokens using *variables* (unknown stay as-is)."""
    def _sub(m):
        key = m.group(1)
        if key in variables:
            return str(variables[key])
        return m.group(0)
    return VAR_RE.sub(_sub, str(text))


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def ordered_nodes(pipeline: RSPipeline) -> list:
    """Execution order: left → right (x), then top → bottom (y)."""
    return sorted(
        (n for n in pipeline.nodes if n.enabled),
        key=lambda n: (round(n.x / 8.0), n.y, n.id),
    )


def build_args(pipeline: RSPipeline) -> list[str]:
    """Flat CLI argument list for direct execution (variables resolved)."""
    args: list[str] = []
    if pipeline.mode == "headless":
        args.append("-headless")
    for node in ordered_nodes(pipeline):
        args.append("-" + node.command)
        for pname, pval in node.params.items():
            if pval in (None, ""):
                continue
            args.append(resolve(pval, pipeline.variables))
    if pipeline.quit:
        args.append("-quit")
    return args


def _bat_quote(value: str) -> str:
    return f'"{value}"' if (" " in value or "%" in value) else value


def build_batch(pipeline: RSPipeline, script_dir: str = ".") -> str:
    """A portable ``.bat`` artifact (keeps ``%VAR%`` + ``set`` lines)."""
    lines = ["@echo off", "REM Generated by IgorVision – RealityScan command centre", ""]
    lines.append('set "RC_EXE=' + pipeline.exe.replace('"', '') + '"')
    lines.append("")
    for name, value in pipeline.variables.items():
        lines.append(f'set "{name}={value}"')
    lines.append("")
    lines.append('"%RC_EXE%"')
    if pipeline.mode == "headless":
        lines.append("  -headless ^")
    nodes = ordered_nodes(pipeline)
    for i, node in enumerate(nodes):
        tail = "" if i == len(nodes) - 1 else " ^"
        parts = ["  -" + node.command]
        for pname, pval in node.params.items():
            if pval in (None, ""):
                continue
            parts.append(_bat_quote(pval))
        lines.append(" ".join(parts) + tail)
    if pipeline.quit:
        lines.append("  -quit")
    lines.append("")
    lines.append("echo [DONE] RealityScan pipeline finished.")
    lines.append("pause")
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# (de)serialisation
# ---------------------------------------------------------------------------

def to_json(pipeline: RSPipeline, path: str) -> None:
    data = {
        "name": pipeline.name,
        "exe": pipeline.exe,
        "mode": pipeline.mode,
        "quit": pipeline.quit,
        "variables": pipeline.variables,
        "nodes": [
            {"id": n.id, "command": n.command, "params": n.params,
             "x": n.x, "y": n.y, "enabled": n.enabled}
            for n in pipeline.nodes
        ],
        "connections": [list(c) for c in pipeline.connections],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def from_json(path: str) -> RSPipeline:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    p = RSPipeline(
        name=data.get("name", "Pipeline"),
        exe=data.get("exe", ""),
        mode=data.get("mode", "gui"),
        quit=bool(data.get("quit", False)),
        variables=dict(data.get("variables", {})),
    )
    for nd in data.get("nodes", []):
        p.nodes.append(RSNode(
            command=nd["command"],
            params=dict(nd.get("params", {})),
            x=float(nd.get("x", 0.0)),
            y=float(nd.get("y", 0.0)),
            enabled=bool(nd.get("enabled", True)),
            id=nd.get("id") or _new_id(),
        ))
    p.connections = [tuple(c) for c in data.get("connections", [])]
    return p


# ---------------------------------------------------------------------------
# import an existing .bat
# ---------------------------------------------------------------------------

def _tokenize(s: str) -> list[str]:
    tokens: list[str] = []
    cur = ""
    inq = False
    for ch in s:
        if ch == '"':
            inq = not inq
            cur += ch
        elif ch.isspace() and not inq:
            if cur:
                tokens.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    return tokens


def from_batch(path: str) -> RSPipeline:
    """Import a RealityScan ``.bat`` into a pipeline (best effort)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    variables: dict[str, str] = {}
    _name_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    for line in raw.splitlines():
        s = line.strip()
        if not s.lower().startswith("set "):
            continue
        body = s[4:].strip()
        # Form A:  set "NAME=value"   (whole assignment quoted, value may have spaces)
        if body.startswith('"') and body.endswith('"') and body.count('"') == 2:
            inner = body[1:-1]
            if "=" in inner:
                name, value = inner.split("=", 1)
                if _name_re.match(name.strip()):
                    variables[name.strip()] = value.strip()
                continue
        # Form B/C:  set NAME="value"   |   set NAME=value
        if "=" in body:
            name, value = body.split("=", 1)
            name = name.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"') and value.count('"') >= 2:
                value = value[1:-1]
            if _name_re.match(name):
                variables[name] = value

    # join ^-continued lines, drop REM/echo/blank
    joined = raw.replace("^ \n", " ").replace("^\n", " ")
    cmd_line = ""
    for line in joined.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith(("rem", "echo", "set ", "@echo", "pause", "if ", "for ")):
            continue
        if "%" in s and ("exe" in low or "rc" in low) and s.count('"') >= 2:
            cmd_line = s
            break
        if s.startswith("-") or (s.startswith('"') and "exe" in low):
            cmd_line = s
            break

    tokens = _tokenize(cmd_line)
    if tokens and tokens[0].strip('"').startswith("%"):
        varname = tokens[0].strip('"').strip("%")
        if varname in variables:
            tokens[0] = variables[varname]
    exe = tokens[0].strip('"') if tokens else ""
    rest = tokens[1:]

    nodes: list[RSNode] = []
    cur_cmd = None
    cur_params: list[str] = []
    x = 0.0

    def _flush():
        nonlocal cur_cmd, cur_params, x
        if cur_cmd is None:
            return
        spec = COMMANDS.get(cur_cmd)
        params: dict = {}
        if spec:
            for i, pval in enumerate(cur_params):
                pname = spec.params[i].name if i < len(spec.params) else f"p{i}"
                params[pname] = pval
        else:
            for i, pval in enumerate(cur_params):
                params[f"p{i}"] = pval
        nodes.append(RSNode(command=cur_cmd, params=params, x=x, y=0.0))
        x += 240.0
        cur_cmd = None
        cur_params = []

    for tok in rest:
        if tok.startswith("-"):
            _flush()
            cur_cmd = tok.lstrip("-")
        else:
            cur_params.append(tok.strip('"'))
    _flush()

    return RSPipeline(
        name="Imported: " + path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
        exe=exe,
        mode="gui",
        quit=False,
        variables=variables,
        nodes=nodes,
    )


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------

def _preset_highdetail() -> RSPipeline:
    steps = [
        ("newScene", {}),
        ("save", {"path": "%PROJECT_FILE%"}),
        ("addFolder", {"folder": "%DISTANCE_FOLDER%"}),
        ("save", {}),
        ("detectMarkers", {}),
        ("defineDistance", {"distance": "%DISTANCE_FILE1%"}),
        ("save", {}),
        ("addFolder", {"folder": "%IMAGE_FOLDER%"}),
        ("align", {}),
        ("selectMaximalComponent", {}),
        ("correctColors", {}),
        ("calculateHighModel", {}),
        ("selectModel", {"name": "Model 1"}),
        ("selectLargestModelComponent", {}),
        ("invertTrianglesSelection", {}),
        ("removeSelectedTriangles", {}),
        ("cleanModel", {}),
        ("selectModel", {"name": "Model 1"}),
        ("deleteSelectedModel", {}),
        ("selectModel", {"name": "Model 2"}),
        ("deleteSelectedModel", {}),
        ("selectModel", {"name": "Model 3"}),
        ("renameSelectedModel", {"name": "HighPolyRaw - Cleaned"}),
        ("save", {}),
    ]
    nodes = [RSNode(command=c, params=dict(p), x=i * 240.0, y=0.0)
             for i, (c, p) in enumerate(steps)]
    return RSPipeline(
        name="HighDetail RAW + Distances",
        exe=r"C:\Program Files\Epic Games\RealityScan_2.1\RealityCapture.exe",
        mode="gui",
        quit=False,
        variables={
            "PROJECT_FILE": "<project folder>\\<name>.rcproj",
            "IMAGE_FOLDER": "<project folder>\\Images",
            "DISTANCE_FOLDER": "<project folder>\\Images\\Distance",
            "DISTANCE_FILE1": "E:\\00.PG.Workspace\\Reality Capture\\000.Distance.Definitions\\DD172173.txt",
        },
        nodes=nodes,
    )


def _preset_align_only() -> RSPipeline:
    steps = [
        ("newScene", {}),
        ("save", {"path": "%PROJECT_FILE%"}),
        ("addFolder", {"folder": "%IMAGE_FOLDER%"}),
        ("align", {}),
        ("selectMaximalComponent", {}),
        ("save", {}),
    ]
    nodes = [RSNode(command=c, params=dict(p), x=i * 240.0, y=0.0)
             for i, (c, p) in enumerate(steps)]
    return RSPipeline(
        name="Align only",
        exe=r"C:\Program Files\Epic Games\RealityScan_2.1\RealityCapture.exe",
        mode="gui",
        quit=False,
        variables={
            "PROJECT_FILE": "<project folder>\\<name>.rcproj",
            "IMAGE_FOLDER": "<project folder>\\Images",
        },
        nodes=nodes,
    )


PRESETS: dict[str, callable] = {
    "HighDetail RAW + Distances": _preset_highdetail,
    "Align only": _preset_align_only,
}


def make_preset(name: str) -> RSPipeline:
    return PRESETS[name]()
