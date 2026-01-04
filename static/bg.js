const canvas = document.getElementById("matrixCanvas");
const ctx = canvas.getContext("2d");

let width, height, dots = [];
const dotSpacing = 40;
const dotSize = 2.2;
const glowRadius = 120;

let cursor = { x: -999, y: -999 };

window.addEventListener("mousemove", e => {
    cursor.x = e.clientX;
    cursor.y = e.clientY;
});

function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;

    dots = [];
    for (let x = 0; x < width; x += dotSpacing) {
        for (let y = 0; y < height; y += dotSpacing) {
            dots.push({ x, y });
        }
    }
}

resize();
window.addEventListener("resize", resize);

function draw() {
    ctx.clearRect(0, 0, width, height);

    for (const dot of dots) {
        const dist = Math.hypot(dot.x - cursor.x, dot.y - cursor.y);
        const glow = Math.max(0, (glowRadius - dist) / glowRadius);

        ctx.beginPath();
        ctx.arc(dot.x, dot.y, dotSize + glow * 4, 0, Math.PI * 2);

        ctx.fillStyle = glow > 0.001
            ? `rgba(${50 + glow * 200}, ${255 * glow}, ${200 + glow * 55}, ${0.35 + glow * 0.6})`
            : "rgba(100, 150, 180, 0.18)";

        ctx.shadowBlur = glow * 25;
        ctx.shadowColor = glow > 0.1 ? `rgba(0,255,200,${glow})` : "transparent";

        ctx.fill();
    }

    requestAnimationFrame(draw);
}

draw();
