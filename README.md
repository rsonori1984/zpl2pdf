# ZPL/TXT/ZIP → PDF (Etiqueta 10x15)

App desktop (Windows, macOS e Linux) que recebe arquivos `.zpl`, `.txt` ou
`.zip`, identifica o conteúdo ZPL, renderiza cada etiqueta localmente
(sem internet) e gera um único **PDF no formato 10x15 cm**, pronto para imprimir.

## O que ele faz

- Aceita `.zpl` e `.txt` com comandos ZPL (um ou vários blocos `^XA...^XZ`)
- Aceita `.zip` com vários `.zpl`/`.txt` dentro
- Renderiza: texto, Code128 (`^BC`), QR Code (`^BQ`), caixas/linhas (`^GB`), blocos de texto (`^FB`)
- 1 página de PDF por etiqueta, sempre com **100 x 150 mm**
- Interface com arrastar-e-soltar
- Escolha de DPI (203 / 300 / 600) — use o mesmo da sua impressora

### Limitações

Implementa um subconjunto do ZPL que cobre a maioria das etiquetas reais.
Comandos raros (gráficos `^GF`, fontes customizadas da impressora, PDF417,
DataMatrix) são ignorados. Se precisar deles, dá para estender o `zpl_parser.py`.

## Imprimir direto

O botão **Imprimir...** renderiza as etiquetas e abre o diálogo de impressão
do sistema (Windows, macOS e Linux/CUPS), onde você escolhe a impressora.
Cada etiqueta sai como uma página de 100x150 mm, sem margens. Usa as
impressoras instaladas no sistema operacional (driver do fabricante).

Dica: se a impressora tiver um papel/etiqueta pré-configurado de 10x15 cm
no driver, selecione-o nas preferências do diálogo; o app pede 100x150 mm,
mas alguns drivers só aceitam os tamanhos cadastrados.

## Rodar (qualquer SO)

Pré-requisito: Python 3.9+.

```bash
cd zpl2pdf
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Gerar executável (.exe / .app / binário)

O PyInstaller empacota para o SO em que roda: gere o `.exe` no Windows, o
`.app` no macOS e o binário no Linux.

**Windows** (cmd/PowerShell, uma linha):

```bat
pyinstaller --noconfirm --clean --windowed --onefile --name "ZPL_para_PDF" --icon "assets\icons\icon.ico" --version-file version_info.txt --add-data "assets;assets" main.py
```

As informações que aparecem em *clique direito no .exe → Propriedades → Detalhes*
(versão, empresa, copyright) vêm do arquivo `version_info.txt`. Ao lançar uma nova
versão, atualize ali `filevers`, `prodvers`, `FileVersion` e `ProductVersion`
(e `APP_VERSION` no `main.py`, para o menu Ajuda → Sobre).

**Linux / macOS**:

```bash
pyinstaller --noconfirm --clean --windowed --onefile \
    --name "ZPL_para_PDF" \
    --icon "assets/icons/icon.ico" \
    --add-data "assets:assets" \
    main.py
```

O resultado fica em `dist/`.

- macOS: se o sistema bloquear o app, libere em Ajustes → Privacidade e Segurança → "Abrir assim mesmo".

## Ajustar o tamanho da etiqueta

Em `converter.py`:

```python
LABEL_WIDTH_MM = 100.0
LABEL_HEIGHT_MM = 150.0
```

## Uso sem interface (lote/depuração)

```python
from converter import convert_files_to_pdf

result = convert_files_to_pdf(["etiquetas.zip"], "saida.pdf", dpi=203)
print(result.total_labels, result.warnings)
```
