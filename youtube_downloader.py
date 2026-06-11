"""
YouTube Downloader Pro — Aplicação Desktop
Interface gráfica com Tkinter (sem navegador, sem localhost)
"""

import tkinter as tk
from tkinter import font as tkfont, filedialog, messagebox, ttk
import threading
import os
import sys
import subprocess
from pathlib import Path

# ── Verificar dependência ──
try:
    import yt_dlp
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "Instalando dependência",
        "O yt-dlp será instalado automaticamente.\nClique OK e aguarde."
    )
    root.destroy()
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
    import yt_dlp

# ════════════════════════════════════════
#  CORES
# ════════════════════════════════════════

CORES = {
    "bg": "#0f0f13",
    "surface": "#1a1a24",
    "surface2": "#1f1f2e",
    "border": "#2a2a3a",
    "accent": "#7c6cfc",
    "accent_hover": "#9588ff",
    "video": "#f72585",
    "audio": "#4cc9f0",
    "correct": "#3ddc84",
    "wrong": "#ff5c5c",
    "warning": "#fca311",
    "text": "#e8e8f0",
    "muted": "#7a7a9a",
    "input_bg": "#151520",
}


# ════════════════════════════════════════
#  FUNÇÕES AUXILIARES
# ════════════════════════════════════════

def sizeof_fmt(num_bytes):
    if num_bytes is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def abrir_pasta(caminho):
    if os.path.exists(caminho):
        if sys.platform == "win32":
            os.startfile(caminho)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", caminho])
        else:
            subprocess.Popen(["xdg-open", caminho])


# ════════════════════════════════════════
#  CLASSE PRINCIPAL
# ════════════════════════════════════════

class YouTubeDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Downloader Pro")
        self.root.configure(bg=CORES["bg"])

        largura, altura = 880, 680
        x = (self.root.winfo_screenwidth() - largura) // 2
        y = (self.root.winfo_screenheight() - altura) // 2
        self.root.geometry(f"{largura}x{altura}+{x}+{y}")
        self.root.minsize(700, 550)

        # Fontes
        self.font_titulo = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        self.font_label = tkfont.Font(family="Segoe UI", size=11)
        self.font_btn = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.font_small = tkfont.Font(family="Segoe UI", size=9)
        self.font_mono = tkfont.Font(family="Consolas", size=10)
        self.font_item = tkfont.Font(family="Segoe UI", size=10)
        self.font_item_bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        # Combobox style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TCombobox",
            fieldbackground=CORES["input_bg"],
            background=CORES["accent"],
            foreground=CORES["text"],
            arrowcolor=CORES["text"],
            bordercolor=CORES["border"],
        )
        style.map("Dark.TCombobox",
            fieldbackground=[("readonly", CORES["input_bg"])],
            foreground=[("readonly", CORES["text"])],
        )

        # Estado
        self.modo = "video"  # video ou audio
        self.pasta_destino = str(Path.home() / "Downloads" / "YT-Downloader")
        os.makedirs(self.pasta_destino, exist_ok=True)
        self.fila = []  # lista de jobs
        self.baixando = False

        self.construir_interface()

    # ──────────────────────────────────
    #  INTERFACE
    # ──────────────────────────────────
    def construir_interface(self):
        # ── TOPO ──
        topo = tk.Frame(self.root, bg=CORES["bg"], padx=24, pady=14)
        topo.pack(fill="x")

        tk.Label(
            topo, text="🎬  YouTube Downloader Pro", font=self.font_titulo,
            bg=CORES["bg"], fg=CORES["text"]
        ).pack(side="left")

        # ── PAINEL DE CONTROLE ──
        painel = tk.Frame(self.root, bg=CORES["surface"], padx=20, pady=16)
        painel.pack(fill="x", padx=24, pady=(0, 8))

        # URL
        url_frame = tk.Frame(painel, bg=CORES["surface"])
        url_frame.pack(fill="x", pady=(0, 10))

        tk.Label(url_frame, text="URL:", font=self.font_label,
                 bg=CORES["surface"], fg=CORES["muted"], width=10, anchor="w").pack(side="left")

        self.entry_url = tk.Entry(
            url_frame, font=self.font_mono,
            bg=CORES["input_bg"], fg=CORES["text"],
            insertbackground=CORES["text"], relief="flat", bd=0,
            highlightthickness=1, highlightcolor=CORES["accent"],
            highlightbackground=CORES["border"]
        )
        self.entry_url.pack(side="left", fill="x", expand=True, ipady=8)

        # Modo (Video / Audio)
        modo_frame = tk.Frame(painel, bg=CORES["surface"])
        modo_frame.pack(fill="x", pady=(0, 10))

        tk.Label(modo_frame, text="Formato:", font=self.font_label,
                 bg=CORES["surface"], fg=CORES["muted"], width=10, anchor="w").pack(side="left")

        self.btn_video = tk.Label(
            modo_frame, text="🎬 MP4 (Vídeo)", font=self.font_btn,
            bg=CORES["video"], fg="#fff", padx=16, pady=6, cursor="hand2"
        )
        self.btn_video.pack(side="left", padx=(0, 8))
        self.btn_video.bind("<Button-1>", lambda e: self.trocar_modo("video"))

        self.btn_audio = tk.Label(
            modo_frame, text="🎵 MP3 (Áudio)", font=self.font_btn,
            bg=CORES["border"], fg=CORES["muted"], padx=16, pady=6, cursor="hand2"
        )
        self.btn_audio.pack(side="left")
        self.btn_audio.bind("<Button-1>", lambda e: self.trocar_modo("audio"))

        # Qualidade
        qual_frame = tk.Frame(painel, bg=CORES["surface"])
        qual_frame.pack(fill="x", pady=(0, 10))

        tk.Label(qual_frame, text="Qualidade:", font=self.font_label,
                 bg=CORES["surface"], fg=CORES["muted"], width=10, anchor="w").pack(side="left")

        self.var_qualidade = tk.StringVar(value="Melhor")
        self.combo_qual = ttk.Combobox(
            qual_frame, textvariable=self.var_qualidade,
            values=["Melhor", "1080p", "720p", "480p", "360p"],
            state="readonly", style="Dark.TCombobox", font=self.font_label, width=15
        )
        self.combo_qual.pack(side="left")

        # Pasta destino
        pasta_frame = tk.Frame(painel, bg=CORES["surface"])
        pasta_frame.pack(fill="x", pady=(0, 10))

        tk.Label(pasta_frame, text="Salvar em:", font=self.font_label,
                 bg=CORES["surface"], fg=CORES["muted"], width=10, anchor="w").pack(side="left")

        self.entry_pasta = tk.Entry(
            pasta_frame, font=self.font_small,
            bg=CORES["input_bg"], fg=CORES["text"],
            insertbackground=CORES["text"], relief="flat", bd=0,
            highlightthickness=1, highlightcolor=CORES["accent"],
            highlightbackground=CORES["border"]
        )
        self.entry_pasta.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry_pasta.insert(0, self.pasta_destino)

        btn_pasta = tk.Label(
            pasta_frame, text="📁", font=self.font_btn,
            bg=CORES["border"], fg=CORES["text"], padx=10, pady=4, cursor="hand2"
        )
        btn_pasta.pack(side="left")
        btn_pasta.bind("<Button-1>", lambda e: self.escolher_pasta())

        btn_abrir = tk.Label(
            pasta_frame, text="Abrir", font=self.font_small,
            bg=CORES["border"], fg=CORES["text"], padx=10, pady=4, cursor="hand2"
        )
        btn_abrir.pack(side="left", padx=(4, 0))
        btn_abrir.bind("<Button-1>", lambda e: abrir_pasta(self.entry_pasta.get()))

        # Botões de ação
        acoes_frame = tk.Frame(painel, bg=CORES["surface"])
        acoes_frame.pack(fill="x")

        self.btn_adicionar = self.criar_botao(
            acoes_frame, "⬇ Adicionar à Fila", self.adicionar_fila, CORES["accent"]
        )
        self.btn_adicionar.pack(side="left", padx=(0, 8))

        self.btn_baixar = self.criar_botao(
            acoes_frame, "🚀 Baixar Tudo", self.baixar_fila, CORES["correct"]
        )
        self.btn_baixar.pack(side="left", padx=(0, 8))

        self.btn_limpar = self.criar_botao(
            acoes_frame, "🗑 Limpar Fila", self.limpar_fila, CORES["wrong"]
        )
        self.btn_limpar.pack(side="left")

        # Status
        self.label_status = tk.Label(
            painel, text="Pronto. Cole o link do YouTube e adicione à fila.",
            font=self.font_small, bg=CORES["surface"], fg=CORES["muted"], anchor="w"
        )
        self.label_status.pack(fill="x", pady=(10, 0))

        # ── FILA DE DOWNLOADS ──
        fila_header = tk.Frame(self.root, bg=CORES["bg"], padx=24)
        fila_header.pack(fill="x", pady=(8, 4))

        tk.Label(fila_header, text="Fila de Downloads", font=self.font_btn,
                 bg=CORES["bg"], fg=CORES["text"]).pack(side="left")

        self.label_contador = tk.Label(
            fila_header, text="0 itens", font=self.font_small,
            bg=CORES["bg"], fg=CORES["muted"]
        )
        self.label_contador.pack(side="right")

        # Lista scrollable
        lista_frame = tk.Frame(self.root, bg=CORES["bg"], padx=24)
        lista_frame.pack(fill="both", expand=True, pady=(0, 12))

        canvas = tk.Canvas(lista_frame, bg=CORES["bg"], highlightthickness=0)
        scrollbar = tk.Scrollbar(lista_frame, orient="vertical", command=canvas.yview)

        self.fila_container = tk.Frame(canvas, bg=CORES["bg"])
        self.fila_container.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.fila_container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def on_mouse(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_mouse)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind para redimensionar a largura do container
        def on_canvas_resize(event):
            canvas.itemconfig(canvas.find_all()[0], width=event.width)
        canvas.bind("<Configure>", on_canvas_resize)

        # Placeholder
        self.placeholder = tk.Label(
            self.fila_container, text="Nenhum download na fila.\nCole um link e clique em 'Adicionar à Fila'.",
            font=self.font_small, bg=CORES["bg"], fg=CORES["muted"], justify="center", pady=40
        )
        self.placeholder.pack(fill="x")

    # ──────────────────────────────────
    #  BOTÃO REUTILIZÁVEL
    # ──────────────────────────────────
    def criar_botao(self, parent, texto, comando, cor):
        fg = "#fff" if cor != CORES["correct"] else "#000"
        btn = tk.Label(parent, text=texto, font=self.font_btn,
                       bg=cor, fg=fg, padx=16, pady=8, cursor="hand2")
        r = min(255, int(cor[1:3], 16) + 30)
        g = min(255, int(cor[3:5], 16) + 30)
        b = min(255, int(cor[5:7], 16) + 30)
        hover = f"#{r:02x}{g:02x}{b:02x}"
        btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda e: btn.configure(bg=cor))
        btn.bind("<Button-1>", lambda e: comando())
        return btn

    # ──────────────────────────────────
    #  TROCAR MODO
    # ──────────────────────────────────
    def trocar_modo(self, modo):
        self.modo = modo
        if modo == "video":
            self.btn_video.configure(bg=CORES["video"], fg="#fff")
            self.btn_audio.configure(bg=CORES["border"], fg=CORES["muted"])
            self.combo_qual.configure(values=["Melhor", "1080p", "720p", "480p", "360p"])
            self.var_qualidade.set("Melhor")
        else:
            self.btn_audio.configure(bg=CORES["audio"], fg="#fff")
            self.btn_video.configure(bg=CORES["border"], fg=CORES["muted"])
            self.combo_qual.configure(values=["320kbps", "192kbps", "128kbps"])
            self.var_qualidade.set("192kbps")

    # ──────────────────────────────────
    #  ESCOLHER PASTA
    # ──────────────────────────────────
    def escolher_pasta(self):
        pasta = filedialog.askdirectory(title="Escolher pasta de destino")
        if pasta:
            self.pasta_destino = pasta
            self.entry_pasta.delete(0, tk.END)
            self.entry_pasta.insert(0, pasta)

    # ──────────────────────────────────
    #  ADICIONAR À FILA
    # ──────────────────────────────────
    def adicionar_fila(self):
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showwarning("Aviso", "Cole o link do YouTube.")
            return

        if "youtube.com" not in url and "youtu.be" not in url:
            messagebox.showwarning("Aviso", "O link não parece ser do YouTube.")
            return

        self.label_status.configure(text="⏳ Buscando informações do vídeo...", fg=CORES["audio"])
        self.root.update()

        qualidade = self.var_qualidade.get()
        modo = self.modo
        pasta = self.entry_pasta.get().strip()
        if not pasta:
            pasta = self.pasta_destino
            self.entry_pasta.insert(0, pasta)

        thread = threading.Thread(target=self._buscar_info, args=(url, modo, qualidade, pasta))
        thread.daemon = True
        thread.start()

    def _buscar_info(self, url, modo, qualidade, pasta):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(url, download=False)

            entries = info.get("entries")
            if entries:
                entries = [e for e in entries if e]
                for entry in entries:
                    titulo = entry.get("title", "Sem título")
                    duracao = entry.get("duration", 0)
                    tamanho = entry.get("filesize_approx") or entry.get("filesize")
                    video_url = entry.get("webpage_url") or entry.get("url")
                    job = {
                        "url": video_url,
                        "titulo": titulo,
                        "duracao": duracao,
                        "tamanho": tamanho,
                        "modo": modo,
                        "qualidade": qualidade,
                        "pasta": pasta,
                        "status": "na_fila",
                        "progresso": 0,
                        "velocidade": "",
                        "eta": "",
                        "frame": None,
                    }
                    self.fila.append(job)
                self.root.after(0, lambda: self._atualizar_fila_ui(
                    f"✅ Playlist '{info.get('title', '')}' — {len(entries)} vídeos adicionados"
                ))
            else:
                titulo = info.get("title", "Sem título")
                duracao = info.get("duration", 0)
                tamanho = info.get("filesize_approx") or info.get("filesize")
                job = {
                    "url": url,
                    "titulo": titulo,
                    "duracao": duracao,
                    "tamanho": tamanho,
                    "modo": modo,
                    "qualidade": qualidade,
                    "pasta": pasta,
                    "status": "na_fila",
                    "progresso": 0,
                    "velocidade": "",
                    "eta": "",
                    "frame": None,
                }
                self.fila.append(job)
                self.root.after(0, lambda: self._atualizar_fila_ui(
                    f"✅ Adicionado: {titulo}"
                ))

            self.root.after(0, lambda: self.entry_url.delete(0, tk.END))

        except Exception as e:
            self.root.after(0, lambda: self.label_status.configure(
                text=f"❌ Erro: {str(e)[:80]}", fg=CORES["wrong"]
            ))

    # ──────────────────────────────────
    #  ATUALIZAR UI DA FILA
    # ──────────────────────────────────
    def _atualizar_fila_ui(self, status_msg=None):
        if status_msg:
            self.label_status.configure(text=status_msg, fg=CORES["correct"])

        # Remover placeholder
        if self.placeholder:
            self.placeholder.pack_forget()

        # Atualizar contador
        self.label_contador.configure(text=f"{len(self.fila)} ite{'m' if len(self.fila) == 1 else 'ns'}")

        # Renderizar itens novos
        for i, job in enumerate(self.fila):
            if job["frame"] is None:
                self._criar_item_fila(job, i)

    def _criar_item_fila(self, job, index):
        is_video = job["modo"] == "video"
        cor_tipo = CORES["video"] if is_video else CORES["audio"]
        icone = "🎬" if is_video else "🎵"
        formato = "MP4" if is_video else "MP3"

        frame = tk.Frame(self.fila_container, bg=CORES["surface"], padx=14, pady=10)
        frame.pack(fill="x", pady=3, padx=2)

        # Linha 1: ícone + título + badge
        top = tk.Frame(frame, bg=CORES["surface"])
        top.pack(fill="x")

        tk.Label(top, text=icone, font=self.font_label,
                 bg=CORES["surface"], fg=CORES["text"]).pack(side="left", padx=(0, 8))

        titulo_label = tk.Label(
            top, text=job["titulo"], font=self.font_item_bold,
            bg=CORES["surface"], fg=CORES["text"], anchor="w"
        )
        titulo_label.pack(side="left", fill="x", expand=True)

        badge = tk.Label(
            top, text="Na fila", font=self.font_small,
            bg=CORES["border"], fg=CORES["muted"], padx=8, pady=2
        )
        badge.pack(side="right")

        # Linha 2: info
        info_frame = tk.Frame(frame, bg=CORES["surface"])
        info_frame.pack(fill="x", pady=(4, 6))

        dur_min = job["duracao"] // 60 if job["duracao"] else 0
        dur_seg = job["duracao"] % 60 if job["duracao"] else 0
        info_parts = [formato, job["qualidade"]]
        if job["duracao"]:
            info_parts.append(f"{dur_min}:{dur_seg:02d}")
        if job["tamanho"]:
            info_parts.append(f"~{sizeof_fmt(job['tamanho'])}")

        info_label = tk.Label(
            info_frame, text="  ·  ".join(info_parts),
            font=self.font_small, bg=CORES["surface"], fg=CORES["muted"], anchor="w"
        )
        info_label.pack(side="left")

        # Linha 3: barra de progresso
        barra_bg = tk.Frame(frame, bg=CORES["border"], height=6)
        barra_bg.pack(fill="x")
        barra_bg.pack_propagate(False)

        barra = tk.Frame(barra_bg, bg=cor_tipo, width=0)
        barra.place(relx=0, rely=0, relwidth=0, relheight=1)

        # Linha 4: status do download
        status_label = tk.Label(
            frame, text="", font=self.font_small,
            bg=CORES["surface"], fg=CORES["muted"], anchor="w"
        )
        status_label.pack(fill="x", pady=(4, 0))

        # Guardar referências
        job["frame"] = frame
        job["widgets"] = {
            "badge": badge,
            "barra": barra,
            "info_label": info_label,
            "status_label": status_label,
        }

    # ──────────────────────────────────
    #  BAIXAR FILA
    # ──────────────────────────────────
    def baixar_fila(self):
        pendentes = [j for j in self.fila if j["status"] == "na_fila"]
        if not pendentes:
            messagebox.showinfo("Aviso", "Nenhum download pendente na fila.")
            return

        if self.baixando:
            return

        self.baixando = True
        self.label_status.configure(text="⏳ Baixando...", fg=CORES["audio"])

        thread = threading.Thread(target=self._processar_fila)
        thread.daemon = True
        thread.start()

    def _processar_fila(self):
        for job in self.fila:
            if job["status"] != "na_fila":
                continue

            job["status"] = "baixando"
            self.root.after(0, lambda j=job: self._atualizar_job_ui(j, "baixando"))

            try:
                self._baixar_video(job)
                job["status"] = "concluido"
                job["progresso"] = 100
                self.root.after(0, lambda j=job: self._atualizar_job_ui(j, "concluido"))
            except Exception as e:
                job["status"] = "erro"
                job["erro"] = str(e)
                self.root.after(0, lambda j=job: self._atualizar_job_ui(j, "erro"))

        self.baixando = False
        self.root.after(0, lambda: self.label_status.configure(
            text="✅ Todos os downloads finalizados!", fg=CORES["correct"]
        ))

    def _baixar_video(self, job):
        pasta = job["pasta"]
        os.makedirs(pasta, exist_ok=True)
        outtmpl = os.path.join(pasta, "%(title).80s.%(ext)s")

        def progress_hook(d):
            if d["status"] == "downloading":
                raw_pct = d.get("_percent_str", "0%").strip().replace("%", "")
                try:
                    pct = float(raw_pct)
                except ValueError:
                    pct = 0.0
                speed = d.get("_speed_str", "").strip()
                eta = d.get("_eta_str", "").strip()
                total_b = d.get("total_bytes") or d.get("total_bytes_estimate")
                down_b = d.get("downloaded_bytes", 0)

                job["progresso"] = pct
                job["velocidade"] = speed
                job["eta"] = eta

                self.root.after(0, lambda: self._atualizar_progresso(
                    job, pct, speed, eta, sizeof_fmt(down_b), sizeof_fmt(total_b)
                ))

            elif d["status"] == "finished":
                job["progresso"] = 99
                self.root.after(0, lambda j=job: self._atualizar_job_ui(j, "processando"))

        if job["modo"] == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "progress_hooks": [progress_hook],
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": job["qualidade"].replace("kbps", ""),
                }],
            }
        else:
            qual = job["qualidade"]
            if qual == "Melhor":
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                h = qual.replace("p", "")
                fmt = (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                       f"/best[height<={h}][ext=mp4]/best[height<={h}]")
            ydl_opts = {
                "format": fmt,
                "outtmpl": outtmpl,
                "progress_hooks": [progress_hook],
                "quiet": True,
                "no_warnings": True,
                "merge_output_format": "mp4",
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job["url"]])

    # ──────────────────────────────────
    #  ATUALIZAR UI DOS JOBS
    # ──────────────────────────────────
    def _atualizar_progresso(self, job, pct, speed, eta, downloaded, total_size):
        if not job.get("widgets"):
            return
        w = job["widgets"]
        w["barra"].place(relx=0, rely=0, relwidth=max(pct / 100, 0.01), relheight=1)

        parts = []
        if downloaded and total_size:
            parts.append(f"{downloaded} / {total_size}")
        if speed:
            parts.append(speed)
        if eta:
            parts.append(f"ETA {eta}")
        parts.append(f"{pct:.1f}%")

        w["status_label"].configure(text="  ·  ".join(parts))

    def _atualizar_job_ui(self, job, status):
        if not job.get("widgets"):
            return
        w = job["widgets"]

        configs = {
            "baixando": ("⬇ Baixando", CORES["audio"], CORES["audio"]),
            "processando": ("⏳ Processando", CORES["warning"], CORES["warning"]),
            "concluido": ("✅ Concluído", CORES["correct"], CORES["correct"]),
            "erro": ("❌ Erro", CORES["wrong"], CORES["wrong"]),
        }

        if status in configs:
            texto, cor_badge, cor_texto = configs[status]
            w["badge"].configure(text=texto, bg=cor_badge, fg="#fff" if status != "concluido" else "#000")

            if status == "concluido":
                w["barra"].place(relx=0, rely=0, relwidth=1, relheight=1)
                w["status_label"].configure(text="Download completo!", fg=CORES["correct"])
            elif status == "erro":
                w["status_label"].configure(
                    text=f"Erro: {job.get('erro', 'desconhecido')[:60]}",
                    fg=CORES["wrong"]
                )

    # ──────────────────────────────────
    #  LIMPAR FILA
    # ──────────────────────────────────
    def limpar_fila(self):
        if self.baixando:
            messagebox.showwarning("Aviso", "Aguarde os downloads terminarem.")
            return

        for job in self.fila:
            if job["frame"]:
                job["frame"].destroy()

        self.fila.clear()
        self.label_contador.configure(text="0 itens")
        self.label_status.configure(text="Fila limpa.", fg=CORES["muted"])
        self.placeholder.pack(fill="x")


# ════════════════════════════════════════
#  INICIAR
# ════════════════════════════════════════

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = YouTubeDownloader(root)
        root.mainloop()
    except Exception as e:
        import traceback
        erro = traceback.format_exc()
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("Erro", f"Ocorreu um erro:\n\n{erro}")
            r.destroy()
        except:
            print(erro)
            input("Pressione Enter para fechar...")
