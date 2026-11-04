#!/usr/bin/env python3
"""Create original setup diagrams, explicitly distinct from console screenshots."""
from html import escape
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/images'
CHECKED = '2026-11-04'
FIGURES = [
    ('setup-overview', 'Google Drive backend setup', 'Keep the same Cloud project through the first four steps.', [
        ('1 · Project', 'Create or select a project', ['Google Cloud Console', 'Record which project owns your client.']),
        ('2 · API', 'Enable Google Drive API', ['APIs & Services → Library', 'Sign-in approval does not enable the API.']),
        ('3 · Consent', 'Configure account access', ['Branding → Audience → Data Access', 'External test app: add your account.']),
        ('4 · Client', 'Create a Desktop app client', ['Google Auth Platform → Clients', 'Download the OAuth client JSON.']),
        ('5 · App', 'Connect, preview, then sync', ['setup --client PATH → preview → sync', 'Automatic sync stays paused until enabled.']),
    ]),
    ('google-project-api', '1–2 · Choose a project and enable Drive', 'Use the project that will own your Desktop OAuth client.', [
        ('Project selector', 'Create or choose your project', ['Example: Omarchy Drive Personal', 'Select it before changing API settings.']),
        ('API Library', 'Find Google Drive API', ['APIs & Services → Library', 'Search for Google Drive API.']),
        ('API details', 'Select Enable', ['Already enabled? The page offers Manage.', 'Allow activation time before retrying setup.']),
        ('Checkpoint', 'Project + Drive API are ready', ['OAuth approval is a separate requirement.', '403 accessNotConfigured points back here.']),
    ]),
    ('google-consent', '3 · Configure Google Auth Platform', 'Example for a personal account using an External app in Testing.', [
        ('Branding', 'Identify your app', ['App name: Omarchy Drive Personal', 'Choose support and contact emails.']),
        ('Audience', 'Allow your account to sign in', ['Choose External for personal Gmail.', 'Under Test users, add your account.']),
        ('Data Access', 'Add the Drive scope', ['https://www.googleapis.com/auth/drive', 'Review the broad read/write permission.']),
        ('Testing note', 'Refresh tokens can expire in 7 days', ['Review publishing requirements for ongoing use.', 'Publishing and Google verification are different.']),
    ]),
    ('google-desktop-client', '4 · Download a Desktop OAuth client', 'The application type matters; the app validates the downloaded JSON.', [
        ('Clients', 'Create client', ['Google Auth Platform → Clients', 'Select Create client.']),
        ('Application type', 'Desktop app', ['Example name: Omarchy Drive Desktop', 'No fixed callback port needs registering.']),
        ('Download', 'Save the client JSON privately', ['Example: ~/Downloads/omarchy-drive-client.json', 'Do not paste its contents into GitHub.']),
        ('Checkpoint', 'Use the downloaded file with setup', ['A Web app client, API key or service account', 'is not a replacement for a Desktop app client.']),
    ]),
    ('connect-and-sync', '5 · Connect and verify the backend', 'The first file transfer happens only when you run sync.', [
        ('Connect', 'setup --client PATH', ['Keep the terminal open during browser sign-in.', 'Approve access using your selected account.']),
        ('Check', 'doctor', ['Look for Local scan: OK and Drive access: OK.', 'Check that the folder pair is the intended one.']),
        ('Review', 'preview', ['Lists uploads, downloads and conflicts.', 'Does not change local or cloud file content.']),
        ('Apply', 'sync → enable automatic sync', ['Run the reviewed first sync.', 'Then enable the panel switch or run resume.']),
    ]),
]


def text(x, y, value, size=18, weight=400, color='#35465b'):
    return f'<text x="{x}" y="{y}" font-family="DejaVu Sans, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(value)}</text>'


def render(name, title, subtitle, cards):
    width, height = 1280, 900 if len(cards) == 5 else 760
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
             f'<title id="title">{escape(title)}</title>',
             '<desc id="desc">Original setup diagram, not a Google Cloud screenshot. Read the linked setup guide for commands and current official references.</desc>',
             f'<rect width="{width}" height="{height}" rx="18" fill="#f2f6fa"/>',
             '<rect x="0" y="0" width="1280" height="12" fill="#166554"/>',
             text(48, 58, 'OMARCHY DRIVE · SETUP GUIDE', 16, 700, '#166554'),
             text(48, 104, title, 32, 700, '#15283a'), text(48, 139, subtitle, 18),
             f'<path d="M68 212V{212 + 132 * (len(cards) - 1)}" stroke="#b7d3ca" stroke-width="3"/>']
    spacing = 132
    for index, (route, label, notes) in enumerate(cards):
        y = 172 + index * spacing
        parts += [f'<rect x="110" y="{y}" width="1122" height="116" rx="12" fill="#ffffff" stroke="#d8e2eb"/>',
                  f'<circle cx="68" cy="{y+40}" r="22" fill="#166554"/>', text(61, y+47, str(index+1), 20, 700, '#ffffff'),
                  text(134, y+27, route.upper(), 13, 700, '#166554'), text(134, y+60, label, 23, 700, '#15283a')]
        for line, note in enumerate(notes):
            parts.append(text(650, y+45+line*28, note, 17))
    parts += [text(48, height-43, 'SETUP DIAGRAM · Not a Google Cloud screenshot · Navigation checked ' + CHECKED, 15, 400, '#526579'),
              text(48, height-20, 'No real accounts, project IDs, credentials or file content are shown.', 13, 400, '#526579'), '</svg>']
    path = OUT / (name + '.svg')
    path.write_text('\n'.join(parts))
    converter = shutil.which('rsvg-convert')
    if converter:
        subprocess.run([converter, str(path), '-o', str(path.with_suffix('.png'))], check=True)
    return path


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for figure in FIGURES:
        print(render(*figure).relative_to(ROOT))
