// DeployCore Remote Management Agent - tray companion.
//
// Purely cosmetic: the actual remote access is DeployCoreAgent.exe, the
// headless service running as SYSTEM (see remote-agent/agent/ and
// PROTOCOL.md - no RustDesk anywhere in this any more). This just gives the
// machine a visible DeployCore identity in the notification area. It also
// doubles as the icon generator: `DeployCoreTray.exe --export-icon <path>` writes
// deploycore.ico, which CI bundles into the MSI (used for the Add/Remove
// Programs entry). One source of truth for the DeployCore mark, drawn with
// GDI+ so no external image tooling is needed to build the icon.
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Windows.Forms;

namespace DeployCoreTray
{
    internal static class Program
    {
        private const string Name = "DeployCore Remote Management Agent";

        [STAThread]
        private static void Main(string[] args)
        {
            if (args.Length >= 2 && args[0] == "--export-icon")
            {
                WriteIco(args[1], new[] { 16, 32, 48, 256 });
                return;
            }

            Application.EnableVisualStyles();

            var menu = new ContextMenuStrip();
            menu.Items.Add(new ToolStripMenuItem(Name) { Enabled = false });
            menu.Items.Add(new ToolStripSeparator());
            var about = new ToolStripMenuItem("About");
            about.Click += (s, e) => MessageBox.Show(
                "This machine is managed remotely by DeployCore.\nThe Remote Management Agent is running.",
                Name, MessageBoxButtons.OK, MessageBoxIcon.Information);
            menu.Items.Add(about);

            using (var mark = DrawMark(32))
            using (var icon = Icon.FromHandle(mark.GetHicon()))
            {
                var notify = new NotifyIcon
                {
                    Icon = icon,
                    Text = Name,
                    Visible = true,
                    ContextMenuStrip = menu,
                };
                Application.Run(new ApplicationContext());
                notify.Visible = false;
            }
        }

        // The DeployCore mark from frontend/public/favicon.svg (32x32 space):
        // three rounded bars in descending blues + a light-blue triangle.
        private static Bitmap DrawMark(int size)
        {
            var bmp = new Bitmap(size, size, PixelFormat.Format32bppArgb);
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.Clear(Color.Transparent);
                float k = size / 32f;

                DrawBar(g, k, 3, 4, 19, 6, Color.FromArgb(0x1d, 0x4e, 0xd8));
                DrawBar(g, k, 3, 13, 19, 6, Color.FromArgb(0x25, 0x63, 0xeb));
                DrawBar(g, k, 3, 22, 19, 6, Color.FromArgb(0x3b, 0x82, 0xf6));

                using (var tri = new SolidBrush(Color.FromArgb(0x38, 0xbd, 0xf8)))
                {
                    g.FillPolygon(tri, new[]
                    {
                        new PointF(22 * k, 22.3f * k),
                        new PointF(30 * k, 25 * k),
                        new PointF(22 * k, 27.7f * k),
                    });
                }
            }
            return bmp;
        }

        private static void DrawBar(Graphics g, float k, float x, float y, float w, float h, Color color)
        {
            float r = 1.5f * k;
            var rect = new RectangleF(x * k, y * k, w * k, h * k);
            using (var path = new GraphicsPath())
            {
                float d = r * 2;
                path.AddArc(rect.X, rect.Y, d, d, 180, 90);
                path.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
                path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
                path.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
                path.CloseFigure();
                using (var brush = new SolidBrush(color))
                    g.FillPath(brush, path);
            }
        }

        // Writes a PNG-compressed .ico (Vista+ accepts PNG frames) with the
        // given sizes - dependency-free, no ImageMagick/Inkscape needed.
        private static void WriteIco(string path, int[] sizes)
        {
            var pngs = new byte[sizes.Length][];
            for (int i = 0; i < sizes.Length; i++)
            {
                using (var b = DrawMark(sizes[i]))
                using (var ms = new MemoryStream())
                {
                    b.Save(ms, ImageFormat.Png);
                    pngs[i] = ms.ToArray();
                }
            }

            using (var fs = new FileStream(path, FileMode.Create))
            using (var bw = new BinaryWriter(fs))
            {
                bw.Write((short)0);            // reserved
                bw.Write((short)1);            // type: icon
                bw.Write((short)sizes.Length); // count
                int offset = 6 + 16 * sizes.Length;
                for (int i = 0; i < sizes.Length; i++)
                {
                    int s = sizes[i];
                    bw.Write((byte)(s >= 256 ? 0 : s)); // width (0 => 256)
                    bw.Write((byte)(s >= 256 ? 0 : s)); // height
                    bw.Write((byte)0);                  // palette
                    bw.Write((byte)0);                  // reserved
                    bw.Write((short)1);                 // planes
                    bw.Write((short)32);                // bpp
                    bw.Write(pngs[i].Length);           // size of PNG data
                    bw.Write(offset);                   // offset
                    offset += pngs[i].Length;
                }
                foreach (var p in pngs)
                    bw.Write(p);
            }
        }
    }
}
