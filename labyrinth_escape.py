# ============================================================
#  Labyrinth Escape  —  CSE423 Computer Graphics Project
#  Procedural mazes · Adaptive AI difficulty · FPP / TPP · Mouse look
# ============================================================
#  W / S           — move forward / backward
#  A / D or ←/→   — turn left / right
#  Mouse           — look around (yaw + pitch / orbit)
#  V               — toggle FPP / TPP camera
#  M               — toggle mini-map
#  P               — pause / resume
#  R               — back to main menu
#  Q  or  ESC      — quit
# ============================================================

from OpenGL.GL   import *
from OpenGL.GLUT import *
from OpenGL.GLU  import *
import math, random

# Window
windowW = 1000
windowH = 800

# World 
CELL_SIZE = 80
WALL_HEIGHT = 90
PLAYER_EYE_Z = 45
PLAYER_RADIUS = 13     # smaller radius = more corridor room, less corner sticking
WALL_WARN_DIST = 30
ENEMY_RADIUS = 20

CATCH_COOLDOWN_MS = 2000
TRANSITION_DURATION = 2500

# Procedural maze sizes (must be odd) 
MAZE_SIZE_L1 = 15
MAZE_SIZE_L2 = 21

# Movement (per-second, applied with delta-time) 
MOVE_SPEED = 200.0    # world units / second
TURN_SPEED_KB = 90.0     # degrees    / second  (keyboard turning)
PITCH_CLAMP = 70.0     # FPP max vertical look angle

# TPP camera 
TPP_ARM = 210.0    # distance behind player
TPP_MIN_PITCH = 15.0   # min elevation angle
TPP_MAX_PITCH = 78.0    # max elevation angle
TPP_LERP = 9.0      # lerp factor — higher = snappier follow

# Mouse
MOUSE_SENSITIVITY = 0.18  # degrees per pixel

# Adaptive difficulty engine ranges
ADAPT_SPEED_MIN = 40  # enemy units/second (easiest)
ADAPT_SPEED_MAX = 130    # enemy units/second — well below player's 200
ADAPT_INTERVAL_MAX = 1100   # ms between enemy BFS recalcs (easiest)
ADAPT_INTERVAL_MIN = 220  # ms between enemy BFS recalcs (hardest)
ADAPT_EVAL_MS = 10000  # re-evaluate every 10 seconds

# Starting pressure for each mode (AI adapts from here)
INITIAL_PRESSURE = {"EASY": 0.08, "MEDIUM": 0.28, "HARD": 0.62}


# Game state
currentMaze = []
currentLevel = 1

playerPos = [0.0, 0.0, 0.0]
playerAngle = 0.0 # yaw (horizontal facing) in degrees
playerPitch = 0.0   # FPP vertical look angle (clamped ±PITCH_CLAMP)
playerLives = 3
playerStart = [0.0, 0.0]

enemyPos = [0.0, 0.0, 0.0]
enemyTargetPos = [0.0, 0.0]
enemyBfsTimer = 0
enemyMoveSpeed = 80.0
enemyStepInterval = 600
enemyStart = [0.0, 0.0]

currentKeys = []          # each: [world_x, world_y, collected_bool]
keysCollected = 0

exitPos = [0.0, 0.0]
exitActive = False

gameOver = False
gameWon = False
showTransition = False
transitionTimer = 0
catchCooldownTimer = 0

# UI state
showMenu = True
showDifficultySelect = False
selectedDifficulty = "MEDIUM"
gamePaused = False
minimapVisible = True

# Camera
cameraMode = "FPP"
tppOrbitPitch = 40.0        # vertical orbit angle for TPP (mouse-controlled)
tppCamPos = [0.0, 0.0, 200.0]  # smooth-lerped camera world position

# Input
keyHeld = {'w': False, 's': False, 'a': False, 'd': False,
           'left': False, 'right': False}
lastFrameMs = 0
mouseWarping = False
cursorHidden = False

# Adaptive difficulty engine
adaptEngine = {
    "levelStartMs":  0,
    "lastEvalMs": 0,
    "catchCount": 0,
    "keyCollectMs": [],      # timestamps of each key pickup during this level
    "pressureScore": 0.42,    # 0.0 = easiest · 1.0 = hardest
}



# Procedural Maze Generation  (iterative DFS recursive-backtracker)

def generate_maze(rows, cols):
    
    """
    Perfect maze via iterative DFS, then a second pass punches ~15% of interior
    walls open to create loops - this gives the player alternate routes and
    makes the maze feel navigable rather than a pure dead-end labyrinth.
    """
    
    grid = [[1] * cols for _ in range(rows)]
    grid[1][1] = 0
    stack = [(1, 1)]

    while stack:
        
        r, c = stack[-1]
        dirs = [(0, 2), (0, -2), (2, 0), (-2, 0)]
        
        random.shuffle(dirs)
        
        moved = False
        
        for dr, dc in dirs:
            
            nr, nc = r + dr, c + dc
            
            if 1 <= nr < rows - 1 and 1 <= nc < cols - 1 and grid[nr][nc] == 1:
                
                grid[r + dr // 2][c + dc // 2] = 0
                grid[nr][nc] = 0
                stack.append((nr, nc))
                moved = True
                break
            
        if not moved:
            
            stack.pop()

    # Extra loop carvings — collect all interior wall cells adjacent to two open cells
    extraWalls = []
    
    for r in range(2, rows - 2):
        
        for c in range(2, cols - 2):
            
            if grid[r][c] == 1:
                
                openNeighbours = sum(
                    1 for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]
                    if grid[r+dr][c+dc] == 0
                )
                
                if openNeighbours >= 2:
                    extraWalls.append((r, c))

    random.shuffle(extraWalls)
    
    toCarve = max(1, len(extraWalls) * 15 // 100)   # open ~15% of eligible walls
    
    for r, c in extraWalls[:toCarve]:
        
        grid[r][c] = 0

    return grid


def bfs_distances(maze, startRow, startCol):
    
    """Returns a 2-D grid of BFS distances from (startRow, startCol). -1 = wall/unreachable."""
    
    rows, cols = len(maze), len(maze[0])
    dist = [[-1] * cols for _ in range(rows)]
    dist[startRow][startCol] = 0
    queue = [(startRow, startCol)]

    while queue:
        
        r, c = queue.pop(0)
        
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            
            nr, nc = r + dr, c + dc
            
            if 0 <= nr < rows and 0 <= nc < cols and maze[nr][nc] == 0 and dist[nr][nc] == -1:
                
                dist[nr][nc] = dist[r][c] + 1
                queue.append((nr, nc))
                
    return dist


def pick_far_cells(distMap, count, minDist, spread=4):
    
    """
    Picks `count` open cells at BFS distance >= minDist, spaced >= spread apart.
    Returns list of (row, col) tuples, sorted farthest-first.
    """
    
    candidates = []
    
    for r in range(len(distMap)):
        
        for c in range(len(distMap[0])):
            
            if distMap[r][c] >= minDist:
                
                candidates.append((distMap[r][c], r, c))
                
    candidates.sort(reverse=True)

    chosen = []
    
    for _, r, c in candidates:
        
        if all(abs(r - cr) + abs(c - cc) >= spread for cr, cc in chosen):
            
            chosen.append((r, c))
            
            if len(chosen) >= count:
                break
    return chosen



# Adaptive Difficulty Engine
def adapt_apply():
    
    """Translates current pressureScore into live enemy speed and BFS interval."""
    
    global enemyMoveSpeed, enemyStepInterval
    p = adaptEngine["pressureScore"]
    enemyMoveSpeed = ADAPT_SPEED_MIN + p * (ADAPT_SPEED_MAX - ADAPT_SPEED_MIN)
    enemyStepInterval = int(ADAPT_INTERVAL_MAX - p * (ADAPT_INTERVAL_MAX - ADAPT_INTERVAL_MIN))


def adapt_evaluate():
    
    """
    Re-evaluates player performance every ADAPT_EVAL_MS and updates pressureScore.
    Metrics: key-collection rate, catch count, time elapsed.
    """
    
    now = glutGet(GLUT_ELAPSED_TIME)
    
    if now - adaptEngine["lastEvalMs"] < ADAPT_EVAL_MS:
        return
    
    adaptEngine["lastEvalMs"] = now

    elapsed = max(1, now - adaptEngine["levelStartMs"])
    elapsedMin = elapsed / 60000.0

    # Normalised key rate: 3 keys/min = full performance score
    keyRate = len(adaptEngine["keyCollectMs"]) / max(elapsedMin, 0.05)
    normKeyRate = min(1.0, keyRate / 3.0)

    # Each catch reduces performance by 0.14, capped at 0.55
    catchPenalty = min(0.55, adaptEngine["catchCount"] * 0.14)

    # Slow time pressure that grows over 5 minutes — keeps things tense even for idle players
    timePressure = min(1.0, elapsed / 300000.0)

    rawPressure = normKeyRate * 0.65 + timePressure * 0.35 - catchPenalty
    rawPressure = max(0.05, min(1.0, rawPressure))

    # Blend 35% toward target so changes are gradual, not jarring
    p = adaptEngine["pressureScore"]
    adaptEngine["pressureScore"] = p + (rawPressure - p) * 0.35
    adapt_apply()




def adapt_on_key_collected():
    
    adaptEngine["keyCollectMs"].append(glutGet(GLUT_ELAPSED_TIME))


def adapt_on_caught():
    
    """Immediately eases difficulty slightly when the player is caught."""
    
    adaptEngine["catchCount"] += 1
    adaptEngine["pressureScore"] = max(0.05, adaptEngine["pressureScore"] - 0.10)
    adapt_apply()



# BFS Pathfinding  (enemy navigation)
def bfs(maze, startCell, goalCell):
    
    """Returns shortest path of (row, col) tuples from start to goal, or []."""
    
    if startCell == goalCell:
        return [startCell]

    queue = [[startCell]]
    visited = {startCell}

    while queue:
        
        path = queue.pop(0)
        r, c = path[-1]
        
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            
            nr, nc = r + dr, c + dc
            
            if 0 <= nr < len(maze) and 0 <= nc < len(maze[0]):
                
                if maze[nr][nc] == 0 and (nr, nc) not in visited:
                    
                    newPath = path + [(nr, nc)]
                    
                    if (nr, nc) == goalCell:
                        return newPath
                    visited.add((nr, nc))
                    queue.append(newPath)
    return []




#  Level Loader  (procedural every run)
def worldCenter(row, col):
    return (col * CELL_SIZE + CELL_SIZE // 2,
            row * CELL_SIZE + CELL_SIZE // 2)


def load_level(levelNum, withTransition=True):
    
    global currentMaze, currentLevel
    global playerPos, playerAngle, playerPitch, playerLives, playerStart
    global enemyPos, enemyTargetPos, enemyBfsTimer, enemyStart
    global currentKeys, keysCollected, exitPos, exitActive
    global gameOver, gameWon, showTransition, transitionTimer, catchCooldownTimer
    global tppCamPos

    currentLevel = levelNum
    gameOver = False
    gameWon = False
    catchCooldownTimer = 0

    mazeSize = MAZE_SIZE_L1 if levelNum == 1 else MAZE_SIZE_L2
    currentMaze = generate_maze(mazeSize, mazeSize)

    if levelNum == 1:
        playerLives = 3

    spawnR, spawnC = 1, 1
    distMap = bfs_distances(currentMaze, spawnR, spawnC)
    maxDist = max(d for row in distMap for d in row if d >= 0)

    # Exit - absolute farthest reachable cell
    exitCells = pick_far_cells(distMap, 1, max(4, maxDist - 3), spread=1)
    exitR, exitC = exitCells[0] if exitCells else (mazeSize - 2, mazeSize - 2)

    # Keys - 3 spread-out cells in the mid-to-far zone
    keyMin = maxDist // 3
    keyCells = pick_far_cells(distMap, 3, keyMin, spread=4)
    
    fb = 1
    
    while len(keyCells) < 3: # fallback if maze is tiny
        
        keyCells.append((fb, 3))
        fb += 2

    # Enemy - far from player, not at exit or key cells
    taken = {(exitR, exitC)} | {(r, c) for r, c in keyCells}
    enemyOptions = pick_far_cells(distMap, 8, max(4, maxDist // 2), spread=2)
    enemyR, enemyC = (mazeSize - 2, mazeSize // 2)
    
    for r, c in enemyOptions:
        
        if (r, c) not in taken:
            enemyR, enemyC = r, c
            break

    px, py = worldCenter(spawnR, spawnC)
    playerPos = [float(px), float(py), 0.0]
    playerAngle = 0.0
    playerPitch = 0.0
    playerStart = [float(px), float(py)]

    ex, ey = worldCenter(exitR, exitC)
    exitPos = [float(ex), float(ey)]

    currentKeys = []
    
    for kr, kc in keyCells:
        
        kx, ky = worldCenter(kr, kc)
        currentKeys.append([float(kx), float(ky), False])

    enX, enY = worldCenter(enemyR, enemyC)
    enemyPos = [float(enX), float(enY), 0.0]
    enemyTargetPos = [float(enX), float(enY)]
    enemyBfsTimer = 0
    enemyStart = [float(enX), float(enY)]

    keysCollected = 0
    exitActive = False

    now = glutGet(GLUT_ELAPSED_TIME)
    adaptEngine["levelStartMs"] = now
    adaptEngine["lastEvalMs"] = now
    adaptEngine["catchCount"] = 0
    adaptEngine["keyCollectMs"] = []
    adapt_apply()

    # Teleport TPP camera directly behind player (no lerp pop-in on load)
    rad = math.radians(playerAngle)
    elev = math.radians(tppOrbitPitch)
    tppCamPos[0] = playerPos[0] - math.cos(rad) * math.cos(elev) * TPP_ARM
    tppCamPos[1] = playerPos[1] - math.sin(rad) * math.cos(elev) * TPP_ARM
    tppCamPos[2] = PLAYER_EYE_Z + math.sin(elev) * TPP_ARM

    if withTransition and levelNum > 1:
        
        showTransition  = True
        transitionTimer = now
        
    else:
        showTransition = False


# Collision
def isWallAt(wx, wy):
    
    col = int(wx // CELL_SIZE)
    row = int(wy // CELL_SIZE)
    
    if row < 0 or row >= len(currentMaze) or col < 0 or col >= len(currentMaze[0]):
        
        return True
    
    return currentMaze[row][col] == 1


def resolve_collision(newX, newY):
    
    """Axis-separated AABB slide — lets the player glide along walls."""
    
    r = PLAYER_RADIUS

    finalX = newX
    
    if isWallAt(newX + r, playerPos[1]) or isWallAt(newX - r, playerPos[1]):
        
        finalX = playerPos[0]

    finalY = newY
    
    if isWallAt(finalX, newY + r) or isWallAt(finalX, newY - r):
        finalY = playerPos[1]

    return finalX, finalY


def is_near_wall():
    
    px, py = playerPos[0], playerPos[1]
    d = WALL_WARN_DIST
    return (isWallAt(px + d, py) or isWallAt(px - d, py) or
            isWallAt(px, py + d) or isWallAt(px, py - d))



# Camera
def setup_fpp_camera():
    
    """First-person: eye at player head, look direction includes pitch."""
    
    rad = math.radians(playerAngle)
    pitch = math.radians(playerPitch)
    
    ex, ey, ez = playerPos[0], playerPos[1], PLAYER_EYE_Z

    lx = ex + math.cos(rad) * math.cos(pitch) * 100
    ly = ey + math.sin(rad) * math.cos(pitch) * 100
    lz = ez + math.sin(pitch) * 100

    gluLookAt(ex, ey, ez,  lx, ly, lz,  0, 0, 1)


def setup_tpp_camera():
    
    """Third-person: camera floats behind/above player (position is lerped)."""
    
    gluLookAt(tppCamPos[0], tppCamPos[1], tppCamPos[2],
              playerPos[0], playerPos[1], PLAYER_EYE_Z + 20,
              0, 0, 1)


def update_tpp_camera(dt):
    
    """Exponential lerp of tppCamPos toward the ideal orbit position."""
    
    rad = math.radians(playerAngle)
    elev = math.radians(tppOrbitPitch)

    tx = playerPos[0] - math.cos(rad) * math.cos(elev) * TPP_ARM
    ty = playerPos[1] - math.sin(rad) * math.cos(elev) * TPP_ARM
    tz = PLAYER_EYE_Z + math.sin(elev) * TPP_ARM

    lf = min(1.0, TPP_LERP * dt)
    tppCamPos[0] += (tx - tppCamPos[0]) * lf
    tppCamPos[1] += (ty - tppCamPos[1]) * lf
    tppCamPos[2] += (tz - tppCamPos[2]) * lf


def setupCamera():
    
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(75, windowW / windowH, 1.0, 3000.0)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    if cameraMode == "FPP":
        setup_fpp_camera()
    else:
        setup_tpp_camera()



#  2D overlay helpers
def begin2D():
    glDisable(GL_DEPTH_TEST)
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(0, windowW, 0, windowH)
    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()


def end2D():
    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST)


def drawScreenText(x, y, text, r=1.0, g=1.0, b=1.0):
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(0, windowW, 0, windowH)
    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()

    glColor3f(r, g, b)
    glRasterPos2f(x, y)
    for ch in text:
        glutBitmapCharacter(GLUT_BITMAP_HELVETICA_18, ord(ch))

    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)


def drawDarkBackground(br=0.05, bg=0.05, bb=0.10):
    glColor3f(br, bg, bb)
    glBegin(GL_QUADS)
    glVertex2f(0, 0);              glVertex2f(windowW, 0)
    glVertex2f(windowW, windowH);  glVertex2f(0, windowH)
    glEnd()




# Menu screens
def draw_menu_screen():
    begin2D()
    drawDarkBackground(0.04, 0.04, 0.08)
    cx, cy = windowW // 2, windowH // 2

    drawScreenText(cx - 135, cy + 135, "LABYRINTH ESCAPE", 0.18, 1.0, 0.58)
    drawScreenText(cx - 110, cy + 103, "CSE423  Computer Graphics",   0.36, 0.36, 0.50)

    glColor3f(0.20, 0.20, 0.34)
    glLineWidth(1)
    glBegin(GL_LINES)
    glVertex2f(cx - 175, cy + 84);  glVertex2f(cx + 175, cy + 84)
    glEnd()

    drawScreenText(cx - 110, cy + 46, "ENTER / SPACE  ->  Start", 0.88, 0.88, 0.88)
    drawScreenText(cx -  74, cy + 12, "Q  /  ESC  ->  Quit", 0.88, 0.88, 0.88)

    drawScreenText(cx - 172, cy - 55,  "Controls:", 0.40, 0.40, 0.54)
    drawScreenText(cx - 172, cy - 80,  "W / S              — move forward / backward", 0.32, 0.32, 0.46)
    drawScreenText(cx - 172, cy - 103, "A / D  or  Arrows  — turn",                   0.32, 0.32, 0.46)
    drawScreenText(cx - 172, cy - 126, "Mouse              — look around freely",      0.32, 0.32, 0.46)
    drawScreenText(cx - 172, cy - 149, "V = FPP/TPP   M = minimap   P = pause",       0.32, 0.32, 0.46)
    drawScreenText(cx - 172, cy - 172, "R = main menu",                                0.32, 0.32, 0.46)

    end2D()


def draw_difficulty_screen():
    begin2D()
    drawDarkBackground(0.04, 0.04, 0.08)
    cx, cy = windowW // 2, windowH // 2

    drawScreenText(cx - 112, cy + 128, "STARTING  DIFFICULTY", 0.18, 1.0, 0.58)
    drawScreenText(cx - 172, cy +  98, "The AI adapts from your choice as you play.", 0.36, 0.36, 0.50)

    glColor3f(0.20, 0.20, 0.34)
    glLineWidth(1)
    glBegin(GL_LINES)
    glVertex2f(cx - 175, cy + 80);  glVertex2f(cx + 175, cy + 80)
    glEnd()

    options = [
        ("1  ->  EASY",   "EASY",   cy + 44, "Enemy starts slow — AI ramps up if you breeze"),
        ("2  ->  MEDIUM", "MEDIUM", cy +  8, "Balanced start — AI calibrates to your pace"),
        ("3  ->  HARD",   "HARD",   cy - 28, "Enemy starts aggressive — earn the escape"),
    ]

    for label, key, yPos, desc in options:
        
        isSelected = selectedDifficulty == key
        
        glColor3f(1.0, 0.85, 0.0) if isSelected else glColor3f(0.46, 0.46, 0.58)

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        gluOrtho2D(0, windowW, 0, windowH)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()
        glRasterPos2f(cx - 86, yPos)
        for ch in label:
            glutBitmapCharacter(GLUT_BITMAP_HELVETICA_18, ord(ch))
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)

        if isSelected:
            
            drawScreenText(cx - 86, yPos - 20, desc, 0.52, 0.76, 0.52)

    drawScreenText(cx - 126, cy - 88, "ENTER / SPACE  ->  Confirm & Start", 0.76, 0.76, 0.76)
    end2D()



# 3D scene
def draw_wall_block(col, row):
    
    x0, y0 = col * CELL_SIZE, row * CELL_SIZE
    x1, y1 = x0 + CELL_SIZE, y0 + CELL_SIZE
    h = WALL_HEIGHT

    glColor3f(0.28, 0.28, 0.40)
    glBegin(GL_QUADS)   # south face
    glVertex3f(x0,y0,0); glVertex3f(x1,y0,0); glVertex3f(x1,y0,h); glVertex3f(x0,y0,h)
    glEnd()
    glBegin(GL_QUADS)   # north face
    glVertex3f(x0,y1,0); glVertex3f(x1,y1,0); glVertex3f(x1,y1,h); glVertex3f(x0,y1,h)
    glEnd()

    glColor3f(0.22, 0.22, 0.33)
    glBegin(GL_QUADS)   # west face
    glVertex3f(x0,y0,0); glVertex3f(x0,y1,0); glVertex3f(x0,y1,h); glVertex3f(x0,y0,h)
    glEnd()
    glBegin(GL_QUADS)   # east face
    glVertex3f(x1,y0,0); glVertex3f(x1,y1,0); glVertex3f(x1,y1,h); glVertex3f(x1,y0,h)
    glEnd()

    glColor3f(0.36, 0.36, 0.50)
    glBegin(GL_QUADS)   # top
    glVertex3f(x0,y0,h); glVertex3f(x1,y0,h); glVertex3f(x1,y1,h); glVertex3f(x0,y1,h)
    glEnd()





def draw_floor_tile(col, row):
    
    shade = 0.03 if (row + col) % 2 == 0 else 0.0
    glColor3f(0.14 + shade, 0.14 + shade, 0.20 + shade)
    x0, y0 = col * CELL_SIZE, row * CELL_SIZE
    glBegin(GL_QUADS)
    glVertex3f(x0, y0, 0); glVertex3f(x0 + CELL_SIZE, y0, 0)
    glVertex3f(x0 + CELL_SIZE, y0+CELL_SIZE, 0); glVertex3f(x0, y0+CELL_SIZE, 0)
    glEnd()


def draw_maze():
    
    for row in range(len(currentMaze)):
        
        for col in range(len(currentMaze[0])):
            
            if currentMaze[row][col] == 1:
                draw_wall_block(col, row)
            else:
                draw_floor_tile(col, row)


def draw_keys():
    
    t = glutGet(GLUT_ELAPSED_TIME) * 0.001
    
    for key in currentKeys:
        
        if not key[2]:
            
            glPushMatrix()
            glTranslatef(key[0], key[1], 25)
            glRotatef(t * 90, 0, 0, 1)
            s = 0.80 + 0.20 * math.sin(t * 3)
            glScalef(s, s, s)
            glColor3f(1.0, 0.85, 0.0)
            glutSolidCube(20)
            glPopMatrix()


def draw_exit():
    
    if not exitActive:
        
        glPushMatrix()
        glTranslatef(exitPos[0], exitPos[1], 0)
        glColor3f(0.36, 0.36, 0.36)
        gluCylinder(gluNewQuadric(), 20, 20, WALL_HEIGHT, 16, 4)
        glPopMatrix()
        
    else:
        
        t = glutGet(GLUT_ELAPSED_TIME) * 0.002
        p = 1.0 + 0.22 * math.sin(t * 4)
        glPushMatrix()
        glTranslatef(exitPos[0], exitPos[1], 0)
        glScalef(p, p, 1.0)
        glColor3f(0.0, 1.0, 0.88)
        gluCylinder(gluNewQuadric(), 22, 6, WALL_HEIGHT, 16, 4)
        glPopMatrix()



def draw_enemy():
    
    pulse = 1.0 + 0.15 * math.sin(glutGet(GLUT_ELAPSED_TIME) * 0.005)
    glPushMatrix()
    glTranslatef(enemyPos[0], enemyPos[1], 0)
    glScalef(pulse, pulse, pulse)

    glColor3f(0.85, 0.08, 0.08)
    gluSphere(gluNewQuadric(), 18, 12, 12)

    glColor3f(0.46, 0.0, 0.0)
    glTranslatef(0, 0, 30)
    gluSphere(gluNewQuadric(), 11, 12, 12)

    glColor3f(1.0, 0.9, 0.0)
    glTranslatef(6, 8, 2);   gluSphere(gluNewQuadric(), 3, 8, 8)
    glTranslatef(-12, 0, 0); gluSphere(gluNewQuadric(), 3, 8, 8)

    glPopMatrix()




def draw_player_body():
    
    """Rendered only in TPP — cylinder torso + sphere head facing playerAngle."""
    
    glPushMatrix()
    glTranslatef(playerPos[0], playerPos[1], 0)
    glRotatef(playerAngle, 0, 0, 1)

    glColor3f(0.18, 0.50, 0.88)
    gluCylinder(gluNewQuadric(), 10, 10, 52, 10, 4)

    glColor3f(0.82, 0.68, 0.54)
    glTranslatef(0, 0, 62)
    gluSphere(gluNewQuadric(), 13, 10, 10)

    glPopMatrix()


# HUD and 2D overlays
def draw_boundary_warning():
    
    if not is_near_wall():
        
        return
    
    t = glutGet(GLUT_ELAPSED_TIME) * 0.005
    intensity = 0.28 + 0.28 * math.sin(t * 6)
    thick = 28

    begin2D()
    glColor3f(intensity, 0.0, 0.0)

    glBegin(GL_QUADS)  # top bar
    glVertex2f(0, windowH-thick); glVertex2f(windowW, windowH-thick)
    glVertex2f(windowW, windowH); glVertex2f(0, windowH)
    glEnd()
    glBegin(GL_QUADS)  # bottom bar
    glVertex2f(0, 0);           glVertex2f(windowW, 0)
    glVertex2f(windowW, thick); glVertex2f(0, thick)
    glEnd()
    glBegin(GL_QUADS)  # left bar
    glVertex2f(0, 0);          glVertex2f(thick, 0)
    glVertex2f(thick, windowH); glVertex2f(0, windowH)
    glEnd()
    glBegin(GL_QUADS)  # right bar
    glVertex2f(windowW-thick, 0); glVertex2f(windowW, 0)
    glVertex2f(windowW, windowH); glVertex2f(windowW-thick, windowH)
    glEnd()

    drawScreenText(windowW // 2 - 78, thick + 10, "! WALL NEARBY !", 1.0, 0.26, 0.26)
    end2D()




def draw_hud_text():
    
    glDisable(GL_DEPTH_TEST)
    
    top = windowH - 30
    pct = int(adaptEngine["pressureScore"] * 100)

    drawScreenText(10, top, f"Keys: {keysCollected} / 3")
    drawScreenText(10, top - 25,  f"Lives: {playerLives}")
    drawScreenText(10, top - 50,  f"Level: {currentLevel}")
    drawScreenText(10, top - 75,  f"AI Pressure: {pct}%", 0.70, 0.70, 0.46)
    drawScreenText(10, top - 100, f"Camera: {cameraMode}  [V]", 0.42, 0.42, 0.56)
    mapHint = "Map: ON [M]" if minimapVisible else "Map: OFF [M]"
    drawScreenText(10, top - 125, mapHint, 0.38, 0.38, 0.52)

    if exitActive and not gameWon:
        drawScreenText(10, top - 158, "EXIT UNLOCKED — find the door!", 0.0, 1.0, 0.88)

    if not gameOver and not gameWon:
        drawScreenText(10, 10, "P=pause   V=camera   M=map   R=menu", 0.26, 0.26, 0.38)

    if gameOver:
        cx, cy = windowW // 2, windowH // 2
        drawScreenText(cx - 80,  cy + 22, "GAME  OVER",         1.0, 0.16, 0.16)
        drawScreenText(cx - 104, cy - 16, "Press R for Menu",   0.88, 0.88, 0.88)

    if gameWon:
        cx, cy = windowW // 2, windowH // 2
        drawScreenText(cx - 95,  cy + 22, "YOU  ESCAPED!",      0.0, 1.0, 0.46)
        drawScreenText(cx - 104, cy - 16, "Press R for Menu",   0.88, 0.88, 0.88)

    glEnable(GL_DEPTH_TEST)


def draw_pause_overlay():
    
    begin2D()
    panelTop = windowH // 2 + 110
    panelBottom = windowH // 2 - 130

    glColor3f(0.04, 0.04, 0.09)
    glBegin(GL_QUADS)
    glVertex2f(0, panelBottom); glVertex2f(windowW, panelBottom)
    glVertex2f(windowW, panelTop); glVertex2f(0, panelTop)
    glEnd()

    glColor3f(0.22, 0.22, 0.40)
    glLineWidth(1)
    glBegin(GL_LINES)
    glVertex2f(0, panelTop); glVertex2f(windowW, panelTop)
    glVertex2f(0, panelBottom); glVertex2f(windowW, panelBottom)
    glEnd()

    cx, cy = windowW // 2, windowH // 2
    pct = int(adaptEngine["pressureScore"] * 100)

    drawScreenText(cx - 56,  cy + 76,  "PAUSED", 0.95, 0.85, 0.18)
    drawScreenText(cx - 120, cy + 36,  "P  ->  Resume", 0.85, 0.85, 0.85)
    drawScreenText(cx - 120, cy -  2,  "R  ->  Main Menu", 0.85, 0.85, 0.85)
    drawScreenText(cx - 120, cy - 40,  "Q  ->  Quit", 0.85, 0.85, 0.85)
    drawScreenText(cx - 152, cy - 92,
                   f"Level: {currentLevel} AI Pressure: {pct}%    Camera: {cameraMode}",
                   0.40, 0.40, 0.54)
    end2D()



def draw_minimap():
    
    MMAP_CELL = 8
    MMAP_MARGIN = 10
    rows = len(currentMaze)
    cols = len(currentMaze[0])
    mmapX = windowW - cols * MMAP_CELL - MMAP_MARGIN
    mmapY = windowH - rows * MMAP_CELL - MMAP_MARGIN

    begin2D()

    for row in range(rows):
        
        for col in range(cols):
            
            x0 = mmapX + col * MMAP_CELL
            y0 = mmapY + (rows - 1 - row) * MMAP_CELL
            
            if currentMaze[row][col] == 1:
                
                glColor3f(0.66, 0.66, 0.76)
                
            else:
                
                glColor3f(0.08, 0.08, 0.13)
                
            glBegin(GL_QUADS)
            glVertex2f(x0, y0); glVertex2f(x0 + MMAP_CELL, y0)
            glVertex2f(x0+MMAP_CELL, y0+MMAP_CELL); glVertex2f(x0, y0+MMAP_CELL)
            glEnd()

    def mmapDot(wx, wy, size, r, g, b):
        
        mc = int(wx // CELL_SIZE)
        mr = int(wy // CELL_SIZE)
        px = mmapX + mc * MMAP_CELL + MMAP_CELL // 2
        py = mmapY + (rows - 1 - mr) * MMAP_CELL + MMAP_CELL // 2
        
        glColor3f(r, g, b)
        glPointSize(size)
        glBegin(GL_POINTS); glVertex2f(px, py); glEnd()

    for key in currentKeys:
        
        if not key[2]:
            mmapDot(key[0], key[1], 5, 1.0, 0.88, 0.0)

    mmapDot(exitPos[0],   exitPos[1],   6, 0.0, 1.0, 0.88)
    mmapDot(enemyPos[0],  enemyPos[1],  7, 1.0, 0.08, 0.08)
    mmapDot(playerPos[0], playerPos[1], 7, 0.0, 1.0, 0.18)

    end2D()


def draw_transition_screen():
    
    begin2D()
    drawDarkBackground(0.05, 0.05, 0.10)
    cx, cy = windowW // 2, windowH // 2
    pct = int(adaptEngine["pressureScore"] * 100)

    glColor3f(0.18, 1.0, 0.46)
    glRasterPos2f(cx - 84, cy + 44)
    for ch in f"LEVEL  {currentLevel}":
        glutBitmapCharacter(GLUT_BITMAP_HELVETICA_18, ord(ch))

    glColor3f(0.72, 0.72, 0.72)
    glRasterPos2f(cx - 130, cy - 2)
    for ch in "A fresh procedural maze has been generated.":
        glutBitmapCharacter(GLUT_BITMAP_HELVETICA_18, ord(ch))

    glColor3f(0.52, 0.72, 0.52)
    glRasterPos2f(cx - 118, cy - 40)
    for ch in f"AI pressure carried over: {pct}%  —  Good luck.":
        glutBitmapCharacter(GLUT_BITMAP_HELVETICA_18, ord(ch))

    end2D()




#  Update logic
def update_player_movement(dt):
    
    global playerAngle, playerPos

    turn = 0
    if keyHeld['a'] or keyHeld['left']:  turn += 1
    if keyHeld['d'] or keyHeld['right']: turn -= 1
    if turn != 0:
        playerAngle = (playerAngle + turn * TURN_SPEED_KB * dt) % 360

    move = 0
    
    if keyHeld['w']: move += 1
    if keyHeld['s']: move -= 1
    
    if move != 0:
        
        rad  = math.radians(playerAngle)
        newX = playerPos[0] + math.cos(rad) * move * MOVE_SPEED * dt
        newY = playerPos[1] + math.sin(rad) * move * MOVE_SPEED * dt
        playerPos[0], playerPos[1] = resolve_collision(newX, newY)


def update_enemy(dt):
    
    global enemyPos, enemyTargetPos, enemyBfsTimer
    global playerLives, gameOver, catchCooldownTimer

    now = glutGet(GLUT_ELAPSED_TIME)

    if now - enemyBfsTimer > enemyStepInterval:
        
        enemyBfsTimer = now
        eRow = int(enemyPos[1] // CELL_SIZE)
        eCol = int(enemyPos[0] // CELL_SIZE)
        pRow = int(playerPos[1] // CELL_SIZE)
        pCol = int(playerPos[0] // CELL_SIZE)
        path = bfs(currentMaze, (eRow, eCol), (pRow, pCol))
        
        if len(path) > 1:
            
            nr, nc = path[1]
            enemyTargetPos = [nc * CELL_SIZE + CELL_SIZE // 2,
                              nr * CELL_SIZE + CELL_SIZE // 2]

    dx, dy = enemyTargetPos[0] - enemyPos[0], enemyTargetPos[1] - enemyPos[1]
    dist = math.hypot(dx, dy)
    
    if dist > 1.0:
        
        step = min(dist, enemyMoveSpeed * dt)
        enemyPos[0] += (dx / dist) * step
        enemyPos[1] += (dy / dist) * step

    catchDist = math.hypot(enemyPos[0] - playerPos[0], enemyPos[1] - playerPos[1])
    
    if catchDist < PLAYER_RADIUS + ENEMY_RADIUS and now > catchCooldownTimer:
        
        catchCooldownTimer = now + CATCH_COOLDOWN_MS
        playerLives -= 1
        adapt_on_caught()

        playerPos[0], playerPos[1] = playerStart[0], playerStart[1]
        enemyPos[0],  enemyPos[1]  = enemyStart[0],  enemyStart[1]
        enemyTargetPos = [enemyStart[0], enemyStart[1]]
        enemyBfsTimer = now

        if playerLives <= 0:
            gameOver = True


def update_keys():
    
    global keysCollected, exitActive
    
    for key in currentKeys:
        
        if not key[2]:
            
            if math.hypot(playerPos[0] - key[0], playerPos[1] - key[1]) < CELL_SIZE * 0.5:
                key[2] = True
                keysCollected += 1
                adapt_on_key_collected()
                
    if keysCollected >= 3:
        exitActive = True



def update_exit():
    
    global gameWon
    
    if not exitActive:
        return
    if math.hypot(playerPos[0] - exitPos[0], playerPos[1] - exitPos[1]) < 36:
        if currentLevel == 1:
            load_level(2, withTransition=True)
        else:
            gameWon = True


def update_cursor_state():
    
    global cursorHidden
    
    shouldHide = not showMenu and not showDifficultySelect
    
    if shouldHide != cursorHidden:
        
        cursorHidden = shouldHide
        glutSetCursor(GLUT_CURSOR_NONE if shouldHide else GLUT_CURSOR_LEFT_ARROW)



# Main render callback
def showScreen():
    glEnable(GL_DEPTH_TEST)
    glClearColor(0.02, 0.02, 0.05, 1.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glLoadIdentity()
    glViewport(0, 0, windowW, windowH)

    if showMenu:
        draw_menu_screen()
        glutSwapBuffers()
        return

    if showDifficultySelect:
        draw_difficulty_screen()
        glutSwapBuffers()
        return

    if showTransition:
        draw_transition_screen()
        glutSwapBuffers()
        return

    setupCamera()
    draw_maze()
    draw_keys()
    draw_exit()

    if not gameOver:
        draw_enemy()

    if cameraMode == "TPP":
        draw_player_body()

    if not gameOver and not gameWon:
        draw_boundary_warning()

    draw_hud_text()

    if minimapVisible:
        draw_minimap()

    if gamePaused:
        draw_pause_overlay()

    glutSwapBuffers()


# Animate  (idle callback — all per-frame logic lives here)
def animate():
    global lastFrameMs, showTransition

    update_cursor_state()

    if showMenu or showDifficultySelect:
        glutPostRedisplay()
        return

    now = glutGet(GLUT_ELAPSED_TIME)
    dt  = min((now - lastFrameMs) / 1000.0, 0.05)   # seconds, capped to avoid spiral of death
    lastFrameMs = now

    if showTransition:
        if now - transitionTimer > TRANSITION_DURATION:
            showTransition = False
        glutPostRedisplay()
        return

    if gamePaused:
        glutPostRedisplay()
        return

    if not gameOver and not gameWon:
        update_player_movement(dt)
        update_tpp_camera(dt)
        update_enemy(dt)
        update_keys()
        update_exit()
        adapt_evaluate()

    glutPostRedisplay()



# Input callbacks
def keyboardListener(key, x, y):
    global showMenu, showDifficultySelect, selectedDifficulty
    global gamePaused, minimapVisible, cameraMode

    if key in (b'q', b'Q', b'\x1b'):
        glutLeaveMainLoop()
        return

    if showMenu:
        if key in (b'\r', b'\n', b' '):
            showMenu = False
            showDifficultySelect = True
        return

    if showDifficultySelect:
        if key == b'1':
            selectedDifficulty = "EASY"
        elif key == b'2':
            selectedDifficulty = "MEDIUM"
        elif key == b'3':
            selectedDifficulty = "HARD"
        elif key in (b'\r', b'\n', b' '):
            adaptEngine["pressureScore"] = INITIAL_PRESSURE[selectedDifficulty]
            showDifficultySelect = False
            load_level(1, withTransition=False)
            glutWarpPointer(windowW // 2, windowH // 2)
        return

    if key in (b'r', b'R'):
        gamePaused           = False
        showMenu             = True
        showDifficultySelect = False
        return

    if key in (b'p', b'P'):
        if not gameOver and not gameWon:
            gamePaused = not gamePaused
        return

    if gamePaused or gameOver or gameWon:
        return

    if key in (b'v', b'V'):
        cameraMode = "TPP" if cameraMode == "FPP" else "FPP"
        return

    if key in (b'm', b'M'):
        minimapVisible = not minimapVisible
        return

    if key in (b'w', b'W'): keyHeld['w'] = True
    if key in (b's', b'S'): keyHeld['s'] = True
    if key in (b'a', b'A'): keyHeld['a'] = True
    if key in (b'd', b'D'): keyHeld['d'] = True


def keyboardUpListener(key, x, y):
    if key in (b'w', b'W'): keyHeld['w'] = False
    if key in (b's', b'S'): keyHeld['s'] = False
    if key in (b'a', b'A'): keyHeld['a'] = False
    if key in (b'd', b'D'): keyHeld['d'] = False


def specialKeyListener(key, x, y):
    if showMenu or showDifficultySelect or gamePaused or gameOver or gameWon:
        return
    if key == GLUT_KEY_LEFT:  keyHeld['left']  = True
    if key == GLUT_KEY_RIGHT: keyHeld['right'] = True


def specialKeyUpListener(key, x, y):
    if key == GLUT_KEY_LEFT:  keyHeld['left']  = False
    if key == GLUT_KEY_RIGHT: keyHeld['right'] = False


def mouseMotionListener(x, y):
    global playerAngle, playerPitch, tppOrbitPitch, mouseWarping

    if mouseWarping:
        mouseWarping = False
        return

    if showMenu or showDifficultySelect or gamePaused or gameOver or gameWon:
        return

    cx, cy = windowW // 2, windowH // 2
    dx = x - cx
    dy = y - cy

    if dx == 0 and dy == 0:
        return

    playerAngle = (playerAngle - dx * MOUSE_SENSITIVITY) % 360

    if cameraMode == "FPP":
        playerPitch = max(-PITCH_CLAMP, min(PITCH_CLAMP, playerPitch - dy * MOUSE_SENSITIVITY))
    else:
        tppOrbitPitch = max(TPP_MIN_PITCH, min(TPP_MAX_PITCH, tppOrbitPitch + dy * MOUSE_SENSITIVITY))

    mouseWarping = True
    glutWarpPointer(cx, cy)


def mouseButtonListener(button, state, x, y):
    pass



# Entry point
def main():
    global lastFrameMs

    glutInit()
    glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGB | GLUT_DEPTH)
    glutInitWindowSize(windowW, windowH)
    glutInitWindowPosition(0, 0)
    glutCreateWindow(b"Labyrinth Escape -- CSE423")

    load_level(1, withTransition=False)
    lastFrameMs = glutGet(GLUT_ELAPSED_TIME)

    glutDisplayFunc(showScreen)
    glutIdleFunc(animate)
    glutKeyboardFunc(keyboardListener)
    glutKeyboardUpFunc(keyboardUpListener)
    glutSpecialFunc(specialKeyListener)
    glutSpecialUpFunc(specialKeyUpListener)
    glutPassiveMotionFunc(mouseMotionListener)
    glutMouseFunc(mouseButtonListener)

    glutWarpPointer(windowW // 2, windowH // 2)
    glutMainLoop()


if __name__ == "__main__":
    main()