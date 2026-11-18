from rdkit import Chem

def candidate_key(smiles):
    mol = Chem.MolFromSmiles(smiles)
    return "valid:" + Chem.MolToSmiles(mol, isomericSmiles=True) if mol is not None else "raw:" + smiles
