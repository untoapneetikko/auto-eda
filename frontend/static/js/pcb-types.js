/**
 * @file PCB Editor Type Definitions — JSDoc typedefs for IDE autocompletion.
 * No runtime code — pure documentation.
 */

/**
 * @typedef {Object} PCBBoard
 * @property {number} width  - Board width (mm)
 * @property {number} height - Board height (mm)
 * @property {string} units  - Measurement units (e.g. "mm")
 */

/**
 * @typedef {Object} PCBPad
 * @property {string} number - Pad number (e.g. "1")
 * @property {string} name   - Pad name (e.g. "INPUT", "GND")
 * @property {number} x      - X offset from component origin (mm)
 * @property {number} y      - Y offset from component origin (mm)
 * @property {'thru_hole'|'smd'} type - Pad type
 * @property {'rect'|'circle'|'oval'} shape - Pad shape
 * @property {number} size_x - Pad width (mm)
 * @property {number} size_y - Pad height (mm)
 * @property {string} net    - Net name this pad belongs to
 * @property {number} [drill] - Drill diameter for thru-hole pads (mm)
 */

/**
 * @typedef {Object} PCBComponent
 * @property {string} id        - Unique identifier (e.g. "U1")
 * @property {string} ref       - Reference designator (e.g. "U1")
 * @property {string} value     - Component value (e.g. "LM7805", "100nF")
 * @property {string} footprint - Footprint name (e.g. "TO-220", "C_0805")
 * @property {number} x         - X position on board (mm)
 * @property {number} y         - Y position on board (mm)
 * @property {number} rotation  - Rotation in degrees
 * @property {string} layer     - Layer placement ("F" or "B")
 * @property {PCBPad[]} pads    - Component pads
 * @property {string} [groupId] - Optional group identifier
 */

/**
 * @typedef {Object} PCBNet
 * @property {string} name     - Net name (e.g. "VIN", "GND")
 * @property {string[]} pads   - Pad references (e.g. ["U1.1", "C1.1"])
 */

/**
 * @typedef {Object} PCBTraceSegment
 * @property {{x: number, y: number}} start - Segment start point
 * @property {{x: number, y: number}} end   - Segment end point
 */

/**
 * @typedef {Object} PCBTrace
 * @property {string} net             - Net name
 * @property {string} layer           - Copper layer (e.g. "F.Cu")
 * @property {number} width           - Trace width (mm)
 * @property {PCBTraceSegment[]} segments - Ordered list of segments
 */

/**
 * @typedef {Object} PCBVia
 * @property {number} x     - X position (mm)
 * @property {number} y     - Y position (mm)
 * @property {number} size  - Outer diameter (mm)
 * @property {number} drill - Drill diameter (mm)
 * @property {string} net   - Net name
 */

/**
 * @typedef {Object} PCBArea
 * @property {number} x1       - Left bound (mm)
 * @property {number} y1       - Top bound (mm)
 * @property {number} x2       - Right bound (mm)
 * @property {number} y2       - Bottom bound (mm)
 * @property {string} layer    - Layer name
 * @property {string} net      - Net name
 * @property {{x: number, y: number}[]} [outline] - Optional polygon outline
 */

/**
 * @typedef {Object} PCBZone
 * @property {string} layer              - Layer name (e.g. "F.Cu")
 * @property {string} net                - Net name
 * @property {{x: number, y: number}[]} points - Zone boundary points
 */

/**
 * @typedef {Object} PCBText
 * @property {string} id         - Unique identifier
 * @property {string} text       - Display text
 * @property {number} x          - X position (mm)
 * @property {number} y          - Y position (mm)
 * @property {number} fontSize   - Font size (mm)
 * @property {string} layer      - Layer name
 * @property {number} [rotation] - Rotation in degrees
 */

/**
 * @typedef {Object} PCBDrawing
 * @property {string} id                   - Unique identifier
 * @property {string} type                 - Drawing type (e.g. "line", "arc", "rect")
 * @property {{x: number, y: number}[]} points - Geometry points
 * @property {string} layer                - Layer name
 * @property {number} [width]              - Line width (mm)
 */

/**
 * @typedef {Object} StackupLayer
 * @property {string} name      - Layer name (e.g. "F.Cu", "Core")
 * @property {'copper'|'dielectric'} type - Layer type
 * @property {number} thickness - Layer thickness (mm)
 * @property {string} material  - Material name (e.g. "Copper", "FR-4")
 */

/**
 * @typedef {Object} DesignRules
 * @property {number} clearance          - Min copper-to-copper clearance (mm)
 * @property {number} minTraceWidth      - Min copper trace width (mm)
 * @property {number} traceWidth         - Default auto-route trace width (mm)
 * @property {number} edgeClearance      - Min clearance from board edge (mm)
 * @property {number} drillClearance     - Min drill-to-drill clearance (mm)
 * @property {number} packageGap         - Min silkscreen-to-silkscreen clearance (mm)
 * @property {number} viaSize            - Via outer diameter (mm)
 * @property {number} viaDrill           - Via drill diameter (mm)
 * @property {number} minAnnularRing     - Min copper ring around hole (mm)
 * @property {number} viaClearance       - Min via-to-via pad clearance (mm)
 * @property {boolean} tentedVias        - Solder mask covers via holes
 * @property {boolean} viaInPad          - Allow vias inside component pads
 * @property {number} boardThickness     - PCB thickness (mm)
 * @property {number} copperWeight       - Copper weight oz/ft²
 * @property {number} layerCount         - Number of copper layers
 * @property {StackupLayer[]} stackup    - Layer stackup definition
 * @property {number} silkscreenWidth    - Min silkscreen line width (mm)
 * @property {number} cornerAngle        - Trace corner angle in degrees
 * @property {number} routeAngleStep     - Allowed trace direction step (degrees)
 * @property {number} minTraceAngle      - Min angle between trace segments (degrees)
 * @property {boolean} allowVias         - Allow autorouter to use vias
 * @property {number} powerTraceWidth    - Min trace width for power nets (mm)
 * @property {number} tracePadClearance  - Min trace-to-pad clearance (mm)
 * @property {number} routeEdgeClearance - Min trace-to-board-edge clearance (mm)
 * @property {number} routingGrid        - Pathfinding grid resolution (mm)
 * @property {number} snapRadius         - Pad/via snap radius (mm)
 * @property {number} silkscreenGap      - Min gap between silkscreen outlines (mm)
 * @property {number} courtyardExpansion - Extra courtyard margin (mm)
 * @property {number} thermalGap         - Extra clearance around thermal components (mm)
 */

/**
 * @typedef {Object} PCBBoardData
 * @property {string} version          - Data format version
 * @property {string} title            - Board title
 * @property {PCBBoard} board          - Board dimensions
 * @property {PCBComponent[]} components - All components
 * @property {PCBNet[]} nets           - Net list
 * @property {PCBTrace[]} traces       - Routed traces
 * @property {PCBVia[]} vias           - Vias
 * @property {PCBArea[]} [areas]       - Copper areas
 * @property {PCBZone[]} [zones]       - Copper zones
 * @property {PCBText[]} [texts]       - Board text items
 * @property {PCBDrawing[]} [drawings] - Board drawings
 */
