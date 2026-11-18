from rdkit.Chem import Draw
from gcms_agent.chemistry.smiles_io import mol_from_graph

def render_top1(path, candidate, peaks):
    from PIL import Image, ImageDraw
    graph = candidate["graph"]
    mol = mol_from_graph(graph, sanitize=True)
    for atom, record in zip(mol.GetAtoms(), graph["atoms"]):
        atom.SetProp("atomNote", record["id"])
    structure = Draw.MolToImage(mol, size=(850, 420))
    canvas = Image.new("RGB", (1000, 800), "white")
    canvas.paste(structure, (75, 30))
    painter = ImageDraw.Draw(canvas)
    painter.text((40, 15), candidate["final_smiles"], fill="black")
    painter.text((40, 470), "EI spectrum (input intensity): green = supported; gray = unassigned", fill="black")
    maximum = max(p["mz"] for p in peaks)
    top = max(p["intensity"] for p in peaks)
    painter.text((40, 490), f"Intensity axis: 0 to {top:g} (original input scale)", fill="black")
    supported = set(candidate["evidence"]["supported_peaks"])
    painter.line((40, 740, 960, 740), fill="black", width=2)
    for peak in peaks:
        x = 40 + 920*peak["mz"]/maximum
        y = 740 - 220*peak["intensity"]/top
        painter.line((x,740,x,y), fill="#12855d" if peak["mz"] in supported else "#888888", width=2)
        if peak["intensity"]/top > .12:
            painter.text((x-5,y-16),str(peak["mz"]),fill="black")
    canvas.save(path)
