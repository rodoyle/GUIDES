import msgpack
import json
import pickle
import os.path
from Queue import PriorityQueue
import re
import doench_score
import azimuth.model_comparison
import numpy as np

class GuideRNA():
  """Holder of gRNA information"""
  def __init__(self, selected, start, seq, PAM, score, exon_ranking, ensembl_gene, gene_name):
    self.start = start
    self.seq = seq
    self.PAM = PAM
    self.score = score
    self.exon_ranking = exon_ranking
    self.ensembl_gene = ensembl_gene
    self.gene_name = gene_name
    self.selected = selected

  def serialize_for_display(self):
    """Serialize for the way we are returning json"""
    return {
      "score": self.score,
      "start": self.start,
      "seq": self.seq,
      "PAM": self.PAM,
      "selected": self.selected,
    }

  def __cmp__(self, other):
    return cmp(self.score, other.score)

params = {
  "PAM": "NGG",
  "protospacer_len": 20,
  "prime5": True,
  "scoring": "Azimuth",
  "quantity": 100
}

# azimuth mdoel
azimuth_saved_model_dir = os.path.join(os.path.dirname(azimuth.__file__), 'saved_models')
model_name = 'V3_model_full.pickle'
azimuth_model_file = os.path.join(azimuth_saved_model_dir, model_name)
with open(azimuth_model_file, 'rb') as f:
  azimuth_model = pickle.load(f)

modPAM = params["PAM"].upper()
modPAM = modPAM.replace('N', '[ATCG]')
params["modPAM"] = modPAM
params["PAM_len"] = len(params["PAM"])

revcompl = lambda x: ''.join([{'A':'T','C':'G','G':'C','T':'A','N':'N'}[B] for B in x][::-1])

def gene_exon_file(gene, exon):
  filename = gene + "_" + str(exon)
  seq_path = os.path.join('../GRCh37_exons/', filename)
  if os.path.isfile(seq_path):
    with open(seq_path) as infile:
      return infile.read()
  else:
    return None

with open('genes_list.json') as genes_list_file:
  genes_list = json.load(genes_list_file)
  # gene format: {"ensembl_id": "ENSG00000261122.2", "name": "5S_rRNA", "description": ""}
  for gene in genes_list:
    exon = 0
    seq = gene_exon_file(gene["ensembl_id"], exon)
    while seq:
      # Check if we haven't done this in a preivous run of the program
      outfile_name = gene["ensembl_id"] + "_" + str(exon) + ".p"
      folder = '../GRCh37_guides_msgpack_' + params["scoring"] + '/'
      output_path = os.path.join(folder, outfile_name)

      if os.path.isfile(output_path):
        # prepare next exon
        exon += 1
        seq = gene_exon_file(gene["ensembl_id"], exon)
        continue

      q = PriorityQueue()
      def process_guide(m, selected, max_queue_size, seq):
        if 'N' in seq:
          return
        PAM_start = m.start()
        score = 0
        if params["scoring"] == "Doench":
          # Doench score requires the 4 before and 6 after 20-mer (gives 30-mer)
          mer30 = seq[PAM_start-params["protospacer_len"]-4:PAM_start+params["PAM_len"]+3]
          if len(mer30) == 30:
            score = doench_score.calc_score(mer30)
        elif params["scoring"] == "Azimuth":
          # Azimuth requires the 4 before and 6 after 20-mer (gives 30-mer)
          mer30 = seq[PAM_start-params["protospacer_len"]-4:PAM_start+params["PAM_len"]+3]
          if len(mer30) == 30:
            score = azimuth.model_comparison.predict(np.array([mer30]), aa_cut=None, percent_peptide=None, model=azimuth_model, model_file=azimuth_model_file)[0]
        protospacer = ""
        PAM = ""
        if params["prime5"]:
          protospacer = seq[PAM_start-params["protospacer_len"]:PAM_start]
          PAM = seq[PAM_start:PAM_start+params["PAM_len"]]
        else:
          protospacer = seq[PAM_start+params["PAM_len"]:PAM_start+params["PAM_len"]+params["protospacer_len"]]
          PAM = seq[PAM_start:PAM_start+params["PAM_len"]]
        potential_gRNA = GuideRNA(selected, PAM_start-params["protospacer_len"], protospacer, PAM, score, exon, gene["ensembl_id"], gene["name"])

        # If there's enough room, add it, no question.
        if q.qsize() < max_queue_size:
          q.put(potential_gRNA)
        # Otherwise, take higher score
        else:
          lowest_gRNA = q.get()
          if potential_gRNA.score > lowest_gRNA.score:
            q.put(potential_gRNA)
          else:
            q.put(lowest_gRNA)

      for m in re.finditer(params["modPAM"], seq):
        if params["prime5"] and (m.start() < params["protospacer_len"] or m.start() + params["PAM_len"] > len(seq)):
          continue
        elif not params["prime5"] and (m.start() + params["PAM_len"] + params["protospacer_len"] > len(seq)):
          continue
        process_guide(m, True, params["quantity"], seq)

      seq_rc = revcompl(seq)

      for m in re.finditer(params["modPAM"], seq_rc):
        if params["prime5"] and (m.start() < params["protospacer_len"] or m.start() + params["PAM_len"] > len(seq)):
          continue
        elif not params["prime5"] and (m.start() + params["PAM_len"] + params["protospacer_len"] > len(seq)):
          continue
        process_guide(m, True, params["quantity"], seq_rc)

      # Pop gRNAs into our 'permanent' storage
      gRNAs = []
      while not q.empty():
        gRNA = q.get()
        gRNAs.append(gRNA.serialize_for_display())
      outfile_name = gene["ensembl_id"] + "_" + str(exon) + ".p"
      folder = '../GRCh37_guides_msgpack_' + params["scoring"] + '/'
      output_path = os.path.join(folder, outfile_name)
      with open(output_path,  'w') as outfile:
        # Reverse gRNAs list.
        # Want highest on-target first.
        msgpack.dump(gRNAs[::-1], outfile)

      # prepare next exon
      exon += 1
      seq = gene_exon_file(gene["ensembl_id"], exon)