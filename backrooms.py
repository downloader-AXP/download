"""backrooms.py - a small raycast maze that does not try very hard."""

import math
import os
import random
import sys
from collections import deque

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import numpy as np
import pygame

W, H = 240, 150
SCALE = 4
TS = 128

MAZE_W, MAZE_H = 21, 21
DAY_SECONDS = 120.0
DAY_MIN_SECONDS = 20.0
DAY_SHRINK = 0.55
NIGHT_SECONDS = 30.0
PLAYER_SPEED = 2.3
TURN_SPEED = 2.2
BOT_SPEED = 1.55

WALL = "png1.png"
FLOOR_DAY = "png2.png"
FLOOR_NIGHT = "png3.png"
CEIL_DAY = "png4.png"
CEIL_NIGHT = "png5.png"
BOTS = tuple("bot%d.png" % i for i in range(1, 11))
SOUND = "sound1.mp3"


def asset_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
        packed = getattr(sys, "_MEIPASS", None)
        if packed:
            candidates.append(packed)
    candidates.append(here)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "RemoteAdmin"))
    for folder in candidates:
        if os.path.exists(os.path.join(folder, "png1.png")):
            return folder
    return candidates[0] if candidates else here


ASSETS = asset_dir()


def placeholder(size, base, specks=((0, 0, 0),)):
    surf = pygame.Surface(size)
    surf.fill(base)
    w, h = size
    for _ in range(280):
        x = random.randrange(w)
        y = random.randrange(h)
        color = random.choice(specks)
        shade = random.randint(-18, 18)
        surf.set_at(
            (x, y),
            (
                max(0, min(255, color[0] + shade)),
                max(0, min(255, color[1] + shade)),
                max(0, min(255, color[2] + shade)),
            ),
        )
    return surf


def load_texture(name, base=(96, 88, 62)):
    try:
        img = pygame.image.load(os.path.join(ASSETS, name))
        return pygame.transform.smoothscale(img.convert(), (TS, TS))
    except Exception:
        return placeholder((TS, TS), base)


def load_bot(name, index):
    try:
        img = pygame.image.load(os.path.join(ASSETS, name))
        return pygame.transform.smoothscale(img.convert_alpha(), (TS * 2, TS * 2))
    except Exception:
        surf = pygame.Surface((TS * 2, TS * 2), pygame.SRCALPHA)
        body = (12, 12, 14) if index % 2 else (30, 26, 28)
        pygame.draw.rect(surf, body, (48, 96, 160, 160))
        pygame.draw.circle(surf, (8, 8, 8), (128, 96), 62)
        pygame.draw.ellipse(surf, (224, 220, 208), (92, 62, 72, 82))
        pygame.draw.ellipse(surf, (0, 0, 0), (104, 84, 14, 18))
        pygame.draw.ellipse(surf, (0, 0, 0), (138, 84, 14, 18))
        pygame.draw.arc(surf, (0, 0, 0), (106, 106, 44, 24), 3.3, 6.1, 5)
        return surf


def make_maze():
    grid = [[1] * MAZE_W for _ in range(MAZE_H)]
    grid[1][1] = 0
    stack = [(1, 1)]
    while stack:
        x, y = stack[-1]
        choices = []
        for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            nx, ny = x + dx, y + dy
            if 0 < nx < MAZE_W - 1 and 0 < ny < MAZE_H - 1 and grid[ny][nx]:
                choices.append((nx, ny, dx, dy))
        if not choices:
            stack.pop()
            continue
        nx, ny, dx, dy = random.choice(choices)
        grid[y + dy // 2][x + dx // 2] = 0
        grid[ny][nx] = 0
        stack.append((nx, ny))
    for y in range(2, MAZE_H - 2):
        for x in range(2, MAZE_W - 2):
            if grid[y][x] == 0 or random.random() > 0.12:
                continue
            across = grid[y][x - 1] == 0 and grid[y][x + 1] == 0
            down = grid[y - 1][x] == 0 and grid[y + 1][x] == 0
            if across != down and (across or down):
                grid[y][x] = 0
    for _ in range(2):
        for y in range(1, MAZE_H - 1):
            for x in range(1, MAZE_W - 1):
                if grid[y][x] != 0:
                    continue
                open_dirs = [
                    (dx, dy)
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    if grid[y + dy][x + dx] == 0
                ]
                if len(open_dirs) != 1 or random.random() > 0.85:
                    continue
                options = []
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if (dx, dy) in open_dirs:
                        continue
                    tx, ty = x + 2 * dx, y + 2 * dy
                    if (
                        0 < tx < MAZE_W - 1
                        and 0 < ty < MAZE_H - 1
                        and grid[ty][tx] == 0
                    ):
                        options.append((x + dx, y + dy))
                if options:
                    wx, wy = random.choice(options)
                    grid[wy][wx] = 0
    return grid


def open_cells(grid):
    return [
        (x, y)
        for y in range(MAZE_H)
        for x in range(MAZE_W)
        if grid[y][x] == 0
    ]


def bfs_path(grid, start, goal):
    seen = {start: None}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        if cell == goal:
            break
        x, y = cell
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (
                0 <= nx < MAZE_W
                and 0 <= ny < MAZE_H
                and grid[ny][nx] == 0
                and (nx, ny) not in seen
            ):
                seen[(nx, ny)] = cell
                queue.append((nx, ny))
    if goal not in seen:
        return [start]
    path = []
    cell = goal
    while cell is not None:
        path.append(cell)
        cell = seen[cell]
    path.reverse()
    return path


class Game:
    def __init__(self):
        pygame.mixer.pre_init(22050, -16, 1, 512)
        pygame.init()
        try:
            pygame.mixer.init()
        except Exception:
            pass
        pygame.display.set_caption("backrooms")
        self.screen = pygame.display.set_mode(
            (W * SCALE, H * SCALE), pygame.FULLSCREEN | pygame.SCALED
        )
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas", 11, bold=True)

        self.wall = load_texture(WALL, (150, 136, 84))
        self.wall_night = self.tint(self.wall, (158, 162, 178))
        self.floor_day = load_texture(FLOOR_DAY, (120, 110, 78))
        self.floor_night = load_texture(FLOOR_NIGHT, (34, 30, 28))
        self.ceil_day = load_texture(CEIL_DAY, (150, 148, 138))
        self.ceil_night = load_texture(CEIL_NIGHT, (20, 19, 22))
        self.bot_images = [load_bot(n, i) for i, n in enumerate(BOTS)]

        self.day_floor_arr = self.tex_array(self.floor_day)
        self.night_floor_arr = self.tex_array(self.floor_night)
        self.day_ceil_arr = self.tex_array(self.ceil_day)
        self.night_ceil_arr = self.tex_array(self.ceil_night)

        self.frame = pygame.Surface((W, H)).convert()
        self.fog = self.make_fog()

        self.music_ready = False
        try:
            sound_path = os.path.join(ASSETS, SOUND)
            if os.path.exists(sound_path):
                pygame.mixer.music.load(sound_path)
                pygame.mixer.music.set_volume(0.8)
                self.music_ready = True
        except Exception:
            self.music_ready = False

        self.zbuf = [60.0] * W
        self.phase = 0
        self.phase_time = 0.0
        self.day_length = DAY_SECONDS
        self.step_timer = 0.0
        self.step_sound = self.make_step_sound()
        self.caught = 0.0
        self.bot_img = self.bot_images[0]
        self.new_round()

    @staticmethod
    def tex_array(surf):
        return np.asarray(pygame.surfarray.array3d(surf)).transpose(1, 0, 2)

    @staticmethod
    def tint(surf, color):
        copy = surf.copy()
        shade = pygame.Surface(copy.get_size())
        shade.fill(color)
        copy.blit(shade, (0, 0), special_flags=pygame.BLEND_MULT)
        return copy

    def make_step_sound(self):
        if not pygame.mixer.get_init():
            return None
        rate = 22050
        total = int(rate * 0.6)
        samples = np.zeros(total, dtype=np.float32)
        count = int(rate * 0.16)
        t = np.arange(count) / rate
        burst = (
            0.5 * np.sin(2 * np.pi * 85.0 * t)
            + 0.3 * np.sin(2 * np.pi * 151.0 * t)
            + 0.45 * np.random.uniform(-1.0, 1.0, count)
        ) * np.exp(-t * 30.0)
        start = int(rate * 0.02)
        samples[start:start + count] += burst
        for delay, gain in ((0.17, 0.45), (0.34, 0.22)):
            offset = int(rate * delay)
            samples[offset:offset + count] += burst * gain
        samples = np.clip(samples, -1.0, 1.0) * 0.55
        try:
            return pygame.mixer.Sound(
                buffer=(samples * 32767).astype(np.int16).tobytes()
            )
        except Exception:
            return None

    def make_fog(self):
        yy, xx = np.mgrid[0:H, 0:W]
        nx = (xx - W / 2.0) / (H / 2.0)
        ny = (yy - H / 2.0) / (H / 2.0)
        radius = np.sqrt(nx * nx + ny * ny)
        edge = np.clip((radius - 0.26) / 0.72, 0.0, 1.0)
        edge = edge * edge * (3.0 - 2.0 * edge)
        alpha = (42.0 + 208.0 * edge).astype(np.uint8)
        arr = np.zeros((H, W, 4), dtype=np.uint8)
        arr[:, :, 3] = alpha
        return pygame.image.frombuffer(arr.tobytes(), (W, H), "RGBA")

    def new_round(self):
        self.maze = make_maze()
        self.cells = open_cells(self.maze)
        start = random.choice(self.cells)
        self.px = start[0] + 0.5
        self.py = start[1] + 0.5
        self.angle = random.uniform(0.0, math.tau)
        self.bot_x = -10.0
        self.bot_y = -10.0
        self.bot_path = []
        self.bot_repick = 0.0
        self.caught = 0.0
        self.day_length = DAY_SECONDS
        self.step_timer = 0.0
        self.set_phase(0)
        self.stop_music()

    def set_phase(self, phase):
        self.phase = phase
        self.phase_time = 0.0
        if phase == 1:
            self.bot_img = random.choice(self.bot_images)
            far = max(
                self.cells,
                key=lambda c: (c[0] + 0.5 - self.px) ** 2
                + (c[1] + 0.5 - self.py) ** 2,
            )
            self.bot_x = far[0] + 0.5
            self.bot_y = far[1] + 0.5
            self.bot_path = []
            self.bot_repick = 0.0
            if self.music_ready:
                try:
                    pygame.mixer.music.play()
                except Exception:
                    pass

    def stop_music(self):
        if self.music_ready:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

    def blocked(self, x, y, radius=0.2):
        for cx in (x - radius, x + radius):
            for cy in (y - radius, y + radius):
                mx, my = int(cx), int(cy)
                if (
                    mx < 0
                    or my < 0
                    or mx >= MAZE_W
                    or my >= MAZE_H
                    or self.maze[my][mx]
                ):
                    return True
        return False

    def update_player(self, dt, keys):
        moving = False
        turn = 0.0
        if keys[pygame.K_LEFT] or keys[pygame.K_q]:
            turn -= 1.0
        if keys[pygame.K_RIGHT] or keys[pygame.K_e]:
            turn += 1.0
        self.angle += turn * TURN_SPEED * dt
        self.angle %= math.tau

        forward = 0.0
        strafe = 0.0
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            forward += 1.0
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            forward -= 1.0
        if keys[pygame.K_a]:
            strafe -= 1.0
        if keys[pygame.K_d]:
            strafe += 1.0

        if forward or strafe:
            moving = True
            length = math.hypot(forward, strafe)
            forward /= length
            strafe /= length
            fx = math.cos(self.angle)
            fy = math.sin(self.angle)
            dx = (fx * forward - fy * strafe) * PLAYER_SPEED * dt
            dy = (fy * forward + fx * strafe) * PLAYER_SPEED * dt
            if not self.blocked(self.px + dx, self.py):
                self.px += dx
            if not self.blocked(self.px, self.py + dy):
                self.py += dy
        return moving

    def update_bot(self, dt):
        if self.phase != 1 or self.caught > 0.0:
            return
        self.bot_repick -= dt
        if self.bot_repick <= 0.0:
            self.bot_repick = 0.5
            start = (int(self.bot_x), int(self.bot_y))
            goal = (int(self.px), int(self.py))
            if 0 <= start[0] < MAZE_W and 0 <= start[1] < MAZE_H:
                self.bot_path = bfs_path(self.maze, start, goal)
            else:
                self.bot_path = []
        if len(self.bot_path) > 1:
            self.bot_path.pop(0)
            target = self.bot_path[0]
            tx, ty = target[0] + 0.5, target[1] + 0.5
        else:
            tx, ty = self.px, self.py
        dx = tx - self.bot_x
        dy = ty - self.bot_y
        distance = math.hypot(dx, dy)
        if distance > 0.001:
            step = min(BOT_SPEED * dt, distance)
            self.bot_x += dx / distance * step
            self.bot_y += dy / distance * step
        if math.hypot(self.px - self.bot_x, self.py - self.bot_y) < 0.55:
            self.caught = 1.7

    def cast_walls(self, dirx, diry, planex, planey, wall):
        for x in range(W):
            camera = 2.0 * x / W - 1.0
            rdx = dirx + planex * camera
            rdy = diry + planey * camera
            mapx, mapy = int(self.px), int(self.py)
            delta_x = abs(1.0 / rdx) if rdx else 1e30
            delta_y = abs(1.0 / rdy) if rdy else 1e30
            if rdx < 0:
                stepx = -1
                sidex = (self.px - mapx) * delta_x
            else:
                stepx = 1
                sidex = (mapx + 1.0 - self.px) * delta_x
            if rdy < 0:
                stepy = -1
                sidey = (self.py - mapy) * delta_y
            else:
                stepy = 1
                sidey = (mapy + 1.0 - self.py) * delta_y
            side = 0
            for _ in range(80):
                if sidex < sidey:
                    sidex += delta_x
                    mapx += stepx
                    side = 0
                else:
                    sidey += delta_y
                    mapy += stepy
                    side = 1
                if (
                    mapx < 0
                    or mapy < 0
                    or mapx >= MAZE_W
                    or mapy >= MAZE_H
                    or self.maze[mapy][mapx]
                ):
                    break
            perp = sidex - delta_x if side == 0 else sidey - delta_y
            if perp < 0.03:
                perp = 0.03
            self.zbuf[x] = perp
            line_h = int(H / perp)
            top = H // 2 - line_h // 2
            tex = wall
            if side == 0:
                wall_x = self.py + perp * rdy
            else:
                wall_x = self.px + perp * rdx
            wall_x -= math.floor(wall_x)
            tex_x = int(wall_x * TS)
            if (side == 0 and rdx > 0) or (side == 1 and rdy < 0):
                tex_x = TS - tex_x - 1
            tex_x = max(0, min(TS - 1, tex_x))
            strip = tex.subsurface(tex_x, 0, 1, TS)
            column = pygame.transform.scale(strip, (1, min(line_h, H * 8)))
            self.frame.blit(column, (x, top))

    def paint_floor_ceiling(self, dirx, diry, planex, planey):
        if self.phase == 1:
            floor_tex, ceil_tex = self.night_floor_arr, self.night_ceil_arr
        else:
            floor_tex, ceil_tex = self.day_floor_arr, self.day_ceil_arr
        ys = np.arange(H, dtype=np.float64) - H / 2.0
        ys[ys == 0.0] = 1.0
        distance = (H / 2.0) / np.abs(ys)
        xs = 2.0 * np.arange(W, dtype=np.float64) / W - 1.0
        rx = dirx + planex * xs
        ry = diry + planey * xs
        wx = self.px + distance[:, None] * rx[None, :]
        wy = self.py + distance[:, None] * ry[None, :]
        tx = (wx * TS).astype(np.int64) & (TS - 1)
        ty = (wy * TS).astype(np.int64) & (TS - 1)
        half = H // 2
        arr = np.empty((H, W, 3), dtype=np.uint8)
        arr[:half] = ceil_tex[ty[:half], tx[:half]]
        arr[half] = floor_tex[ty[half], tx[half]]
        arr[half + 1:] = floor_tex[ty[half + 1:], tx[half + 1:]]
        pygame.surfarray.blit_array(self.frame, np.ascontiguousarray(arr.transpose(1, 0, 2)))

    def draw_bot(self, dirx, diry, planex, planey):
        if self.phase != 1 and self.caught <= 0.0:
            return
        if self.caught > 0.0:
            return
        sx = self.bot_x - self.px
        sy = self.bot_y - self.py
        det = planex * diry - dirx * planey
        if abs(det) < 1e-6:
            return
        inv = 1.0 / det
        tx = inv * (diry * sx - dirx * sy)
        ty = inv * (-planey * sx + planex * sy)
        if ty <= 0.08:
            return
        screen_x = int((W / 2.0) * (1.0 + tx / ty))
        size = abs(int(H / ty))
        top = H // 2 - size // 2
        if size < 2 or size > H * 6:
            return
        img = pygame.transform.scale(self.bot_img, (size, size))
        left = screen_x - size // 2
        for x in range(max(0, left), min(W, left + size)):
            if self.zbuf[x] > ty:
                self.frame.blit(img, (x, top), (x - left, 0, 1, size))

    def draw_fog(self):
        self.frame.blit(self.fog, (0, 0))

    def draw_timers(self):
        if self.phase == 0:
            left = max(0.0, self.day_length - self.phase_time)
            label = "DAY %02d:%02d" % (int(left // 60), int(left % 60))
        else:
            left = max(0.0, NIGHT_SECONDS - self.phase_time)
            label = "NIGHT %02d:%02d" % (int(left // 60), int(left % 60))
        shadow = self.font.render(label, True, (0, 0, 0))
        text = self.font.render(label, True, (235, 235, 225))
        x = W // 2 - text.get_width() // 2
        self.frame.blit(shadow, (x + 1, 5))
        self.frame.blit(text, (x, 4))

    def draw_meter(self):
        if self.phase != 1:
            return
        distance = math.hypot(self.px - self.bot_x, self.py - self.bot_y)
        label = "BOT %.1f m" % distance
        shadow = self.font.render(label, True, (0, 0, 0))
        text = self.font.render(label, True, (235, 235, 225))
        x = W // 2 - text.get_width() // 2
        self.frame.blit(shadow, (x + 1, H - 13))
        self.frame.blit(text, (x, H - 14))

    def draw_caught(self):
        big = pygame.transform.smoothscale(self.bot_img, (H * 2, H * 2))
        rect = big.get_rect(center=(W // 2, H // 2))
        self.frame.fill((0, 0, 0))
        self.frame.blit(big, rect)
        flash = pygame.Surface((W, H), pygame.SRCALPHA)
        flash.fill((90, 0, 0, 90))
        self.frame.blit(flash, (0, 0))

    def run(self):
        running = True
        while running:
            dt = min(self.clock.tick(60) / 1000.0, 0.05)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            keys = pygame.key.get_pressed()
            if self.caught > 0.0:
                self.caught -= dt
                if self.caught <= 0.0:
                    self.new_round()
                    continue
                self.draw_caught()
            else:
                moving = self.update_player(dt, keys)
                if moving:
                    self.step_timer -= dt
                    if self.step_timer <= 0.0:
                        self.step_timer = 0.38 + random.uniform(0.0, 0.08)
                        if self.step_sound:
                            self.step_sound.play()
                else:
                    self.step_timer = 0.0
                self.update_bot(dt)
                if self.caught <= 0.0:
                    self.phase_time += dt
                    if self.phase == 0 and self.phase_time >= self.day_length:
                        self.set_phase(1)
                    elif self.phase == 1 and self.phase_time >= NIGHT_SECONDS:
                        self.day_length = max(
                            DAY_MIN_SECONDS, self.day_length * DAY_SHRINK
                        )
                        self.set_phase(0)

                dirx = math.cos(self.angle)
                diry = math.sin(self.angle)
                planex = -diry * 0.66
                planey = dirx * 0.66
                self.paint_floor_ceiling(dirx, diry, planex, planey)
                self.cast_walls(
                    dirx,
                    diry,
                    planex,
                    planey,
                    self.wall_night if self.phase == 1 else self.wall,
                )
                self.draw_bot(dirx, diry, planex, planey)
                if self.phase == 1:
                    self.draw_fog()
                self.draw_timers()
                self.draw_meter()

            pygame.transform.scale(self.frame, (W * SCALE, H * SCALE), self.screen)
            pygame.display.flip()

        self.stop_music()
        pygame.quit()


if __name__ == "__main__":
    Game().run()
