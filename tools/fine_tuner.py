"""
tools/fine_tuner.py

Tier 2 — Feature 4: Fine-tune Review Model on YOUR codebase

Step 1: Collect training data from your past PR reviews
Step 2: Format into instruction-tuning JSONL format
Step 3: Fine-tune Llama 3 using Unsloth (free, fast, runs on Colab)
Step 4: Use your fine-tuned model for reviews

Why this matters:
  Generic LLMs give generic advice.
  A model fine-tuned on YOUR reviews learns:
    - Your team's coding standards
    - Your tech stack patterns
    - What you consider critical vs minor
    - Your codebase's architecture decisions

Usage:
  # Step 1: Collect training data from your reports
  python tools/fine_tuner.py --collect --reports-dir reports/

  # Step 2: Generate fine-tuning JSONL
  python tools/fine_tuner.py --prepare

  # Step 3: Upload to Google Colab and run fine-tuning
  python tools/fine_tuner.py --colab-notebook

  # Step 4: Test your fine-tuned model
  python tools/fine_tuner.py --test
"""
from __future__ import annotations

import json
import argparse
from pathlib import Path
from datetime import datetime


TRAINING_DATA_PATH = Path("./training_data")
DATASET_PATH       = TRAINING_DATA_PATH / "dataset.jsonl"
STATS_PATH         = TRAINING_DATA_PATH / "stats.json"

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT FOR FINE-TUNING
# ─────────────────────────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """\
You are a senior software engineer performing precise, evidence-based code reviews.

RULES:
1. Only report issues present in the diff
2. Quote the exact problematic code in your message
3. Provide concrete, working fix code
4. Score from 0.0 (worst) to 1.0 (best)
5. Return ONLY valid JSON

OUTPUT SCHEMA:
{
  "findings": [
    {
      "line": <int>,
      "severity": "critical|warning|info",
      "category": "security|bug|quality|performance|docs",
      "message": "<issue quoting offending code>",
      "fix": "<concrete replacement code>"
    }
  ],
  "summary": "<2-3 sentences>",
  "overall_score": <float 0.0-1.0>,
  "test_coverage_gaps": ["<missing test>"]
}"""


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: COLLECT TRAINING DATA FROM REPORTS
# ─────────────────────────────────────────────────────────────────────────────

class TrainingDataCollector:
    """
    Reads your existing review reports and converts them into
    (prompt, completion) pairs for fine-tuning.

    Each pair = one file review:
      prompt     = the diff + context shown to the LLM
      completion = the JSON review the LLM should output
    """

    def collect(self, reports_dir: str = "reports") -> int:
        TRAINING_DATA_PATH.mkdir(parents=True, exist_ok=True)

        reports    = list(Path(reports_dir).glob("*.json"))
        pairs      = []
        skipped    = 0

        print(f"[fine-tuner] Scanning {len(reports)} reports...")

        for report_path in reports:
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                new_pairs = self._extract_pairs(report)
                pairs.extend(new_pairs)
            except Exception as e:
                print(f"  [skip] {report_path.name}: {e}")
                skipped += 1

        # Write JSONL
        with open(DATASET_PATH, "w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")

        # Save stats
        stats = {
            "total_pairs":    len(pairs),
            "reports_used":   len(reports) - skipped,
            "reports_skipped": skipped,
            "collected_at":   datetime.utcnow().isoformat(),
        }
        STATS_PATH.write_text(json.dumps(stats, indent=2))

        print(f"[fine-tuner] Collected {len(pairs)} training pairs")
        print(f"[fine-tuner] Saved to {DATASET_PATH}")
        return len(pairs)

    def _extract_pairs(self, report: dict) -> list[dict]:
        pairs = []

        for file_rev in report.get("files", []):
            filename = file_rev.get("file", "")
            review   = file_rev.get("review", {})

            # Skip empty or failed reviews
            if not review.get("findings") and not review.get("summary"):
                continue

            # Reconstruct what the prompt would have looked like
            patch   = ""
            content = ""

            # Find the file's patch from findings context
            for f in review.get("findings", []):
                if f.get("file") == filename:
                    break

            prompt = self._build_training_prompt(
                filename = filename,
                pr_title = report.get("pr_title", ""),
                patch    = patch,
                content  = content,
            )

            completion = json.dumps({
                "findings":          review.get("findings", []),
                "summary":           review.get("summary", ""),
                "overall_score":     review.get("overall_score", 1.0),
                "test_coverage_gaps": review.get("test_coverage_gaps", []),
            }, ensure_ascii=False)

            # Only include if there's meaningful content
            if len(completion) > 50:
                pairs.append({
                    "prompt":     prompt,
                    "completion": completion,
                    "metadata": {
                        "repo":     report.get("repo", ""),
                        "pr":       report.get("pr_number", 0),
                        "file":     filename,
                        "score":    review.get("overall_score", 1.0),
                        "critical": sum(
                            1 for f in review.get("findings", [])
                            if f.get("severity") == "critical"
                        ),
                    }
                })

        return pairs

    def _build_training_prompt(
        self,
        filename: str,
        pr_title: str,
        patch:    str,
        content:  str,
    ) -> str:
        return (
            f"=== PR INFORMATION ===\n"
            f"Title: {pr_title}\n"
            f"File: {filename}\n\n"
            f"=== DIFF TO REVIEW ===\n"
            f"{patch or '[diff not available in report]'}\n\n"
            f"=== FULL FILE ===\n"
            f"{content[:2000] or '[content not available in report]'}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: FORMAT INTO ALPACA / LLAMA-3 INSTRUCTION FORMAT
# ─────────────────────────────────────────────────────────────────────────────

class DatasetFormatter:
    """
    Converts raw (prompt, completion) pairs into the format
    expected by Unsloth / HuggingFace fine-tuning.

    Supports:
      - Alpaca format (most common)
      - ShareGPT / ChatML format (for chat models)
    """

    def format_alpaca(self, output_path: str = None) -> Path:
        """Alpaca format: instruction + input + output."""
        if not DATASET_PATH.exists():
            raise FileNotFoundError(
                "No training data found. Run --collect first."
            )

        pairs    = [
            json.loads(line)
            for line in DATASET_PATH.read_text().splitlines()
            if line.strip()
        ]

        out_path = Path(output_path or TRAINING_DATA_PATH / "alpaca_dataset.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for pair in pairs:
                record = {
                    "instruction": REVIEW_SYSTEM_PROMPT,
                    "input":       pair["prompt"],
                    "output":      pair["completion"],
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"[fine-tuner] Alpaca format: {len(pairs)} records → {out_path}")
        return out_path

    def format_sharegpt(self, output_path: str = None) -> Path:
        """ShareGPT/ChatML format for chat fine-tuning."""
        if not DATASET_PATH.exists():
            raise FileNotFoundError(
                "No training data found. Run --collect first."
            )

        pairs    = [
            json.loads(line)
            for line in DATASET_PATH.read_text().splitlines()
            if line.strip()
        ]

        out_path = Path(output_path or TRAINING_DATA_PATH / "sharegpt_dataset.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for pair in pairs:
                record = {
                    "conversations": [
                        {"from": "system",    "value": REVIEW_SYSTEM_PROMPT},
                        {"from": "human",     "value": pair["prompt"]},
                        {"from": "assistant", "value": pair["completion"]},
                    ]
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"[fine-tuner] ShareGPT format: {len(pairs)} records → {out_path}")
        return out_path

    def get_stats(self) -> dict:
        if not STATS_PATH.exists():
            return {}
        return json.loads(STATS_PATH.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: GOOGLE COLAB NOTEBOOK GENERATOR
# Generates a ready-to-run Colab notebook for fine-tuning
# ─────────────────────────────────────────────────────────────────────────────

COLAB_NOTEBOOK = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10.0"},
        "accelerator": "GPU",
        "colab": {"gpuType": "T4"},
    },
    "cells": [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# Fine-tune Llama 3 for AI Code Review\n",
                "This notebook fine-tunes Llama-3.1-8B on your PR review data.\n",
                "Runtime: T4 GPU (free tier) — ~20 mins for 100 examples\n",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# Install Unsloth (fastest fine-tuning library)\n",
                "!pip install unsloth\n",
                "!pip install datasets transformers trl peft bitsandbytes",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "from unsloth import FastLanguageModel\n",
                "import torch\n",
                "\n",
                "max_seq_length = 2048\n",
                "dtype          = None  # auto-detect\n",
                "load_in_4bit   = True  # 4-bit quantization (fits in free GPU)\n",
                "\n",
                "model, tokenizer = FastLanguageModel.from_pretrained(\n",
                "    model_name     = 'unsloth/Meta-Llama-3.1-8B',\n",
                "    max_seq_length = max_seq_length,\n",
                "    dtype          = dtype,\n",
                "    load_in_4bit   = load_in_4bit,\n",
                ")\n",
                "print('Model loaded!')",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# Add LoRA adapters (only train ~1% of parameters)\n",
                "model = FastLanguageModel.get_peft_model(\n",
                "    model,\n",
                "    r              = 16,       # LoRA rank\n",
                "    target_modules = ['q_proj', 'k_proj', 'v_proj',\n",
                "                       'o_proj', 'gate_proj', 'up_proj', 'down_proj'],\n",
                "    lora_alpha     = 16,\n",
                "    lora_dropout   = 0,\n",
                "    bias           = 'none',\n",
                "    use_gradient_checkpointing = 'unsloth',\n",
                "    random_state   = 42,\n",
                ")\n",
                "print(f'Trainable params: {sum(p.numel() for p in model.parameters() if p.requires_grad):,}')",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# Upload your dataset file here\n",
                "from google.colab import files\n",
                "uploaded = files.upload()  # upload alpaca_dataset.jsonl\n",
                "\n",
                "from datasets import load_dataset\n",
                "dataset = load_dataset('json', data_files='alpaca_dataset.jsonl', split='train')\n",
                "print(f'Dataset: {len(dataset)} examples')",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "from trl import SFTTrainer\n",
                "from transformers import TrainingArguments\n",
                "\n",
                "def format_prompt(example):\n",
                "    return {\n",
                "        'text': (\n",
                "            f'### System:\\n{example[\"instruction\"]}\\n\\n'\n",
                "            f'### Input:\\n{example[\"input\"]}\\n\\n'\n",
                "            f'### Response:\\n{example[\"output\"]}'\n",
                "        )\n",
                "    }\n",
                "\n",
                "dataset = dataset.map(format_prompt)\n",
                "\n",
                "trainer = SFTTrainer(\n",
                "    model     = model,\n",
                "    tokenizer = tokenizer,\n",
                "    train_dataset = dataset,\n",
                "    dataset_text_field = 'text',\n",
                "    max_seq_length     = max_seq_length,\n",
                "    args = TrainingArguments(\n",
                "        per_device_train_batch_size  = 2,\n",
                "        gradient_accumulation_steps  = 4,\n",
                "        warmup_steps    = 10,\n",
                "        num_train_epochs = 3,\n",
                "        learning_rate   = 2e-4,\n",
                "        fp16            = not torch.cuda.is_bf16_supported(),\n",
                "        bf16            = torch.cuda.is_bf16_supported(),\n",
                "        logging_steps   = 10,\n",
                "        output_dir      = 'outputs',\n",
                "        optim           = 'adamw_8bit',\n",
                "        save_strategy   = 'epoch',\n",
                "    ),\n",
                ")\n",
                "\n",
                "trainer.train()\n",
                "print('Training complete!')",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# Save the fine-tuned model\n",
                "model.save_pretrained('code-review-llama3')\n",
                "tokenizer.save_pretrained('code-review-llama3')\n",
                "\n",
                "# Push to HuggingFace Hub (optional)\n",
                "# model.push_to_hub('your-username/code-review-llama3')\n",
                "\n",
                "# Download the model\n",
                "import shutil\n",
                "shutil.make_archive('code-review-llama3', 'zip', 'code-review-llama3')\n",
                "files.download('code-review-llama3.zip')\n",
                "print('Model saved and downloaded!')",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# Test your fine-tuned model\n",
                "FastLanguageModel.for_inference(model)\n",
                "\n",
                "test_diff = '''\n",
                "@@ -0,0 +1,3 @@\n",
                "+password = 'admin123'\n",
                "+query = f\"SELECT * FROM users WHERE name='{username}'\"\n",
                "'''\n",
                "\n",
                "inputs = tokenizer(\n",
                "    f'### System:\\n{SYSTEM}\\n\\n### Input:\\nFile: test.py\\n\\nDiff:\\n{test_diff}\\n\\n### Response:',\n",
                "    return_tensors='pt'\n",
                ").to('cuda')\n",
                "\n",
                "outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)\n",
                "print(tokenizer.decode(outputs[0], skip_special_tokens=True))",
            ],
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fine-tune Llama 3 on your PR review data"
    )
    parser.add_argument("--collect",  action="store_true",
                        help="Collect training data from reports")
    parser.add_argument("--prepare",  action="store_true",
                        help="Format dataset for fine-tuning")
    parser.add_argument("--colab-notebook", action="store_true",
                        help="Generate Google Colab notebook")
    parser.add_argument("--stats",    action="store_true",
                        help="Show dataset statistics")
    parser.add_argument("--reports-dir", default="reports",
                        help="Directory containing review reports")
    args = parser.parse_args()

    if args.collect:
        collector = TrainingDataCollector()
        n = collector.collect(args.reports_dir)
        print(f"\n✅ Collected {n} training pairs")
        print(f"   Next: python tools/fine_tuner.py --prepare")

    elif args.prepare:
        formatter = DatasetFormatter()
        alpaca    = formatter.format_alpaca()
        sharegpt  = formatter.format_sharegpt()
        print(f"\n✅ Dataset ready:")
        print(f"   Alpaca:   {alpaca}")
        print(f"   ShareGPT: {sharegpt}")
        print(f"   Next: python tools/fine_tuner.py --colab-notebook")

    elif args.colab_notebook:
        import json as _json
        out = TRAINING_DATA_PATH / "fine_tune_colab.ipynb"
        TRAINING_DATA_PATH.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(COLAB_NOTEBOOK, indent=2))
        print(f"\n✅ Colab notebook saved: {out}")
        print(f"   1. Upload {out} to Google Colab")
        print(f"   2. Upload training_data/alpaca_dataset.jsonl when prompted")
        print(f"   3. Run all cells (T4 GPU, ~20 mins for 100 examples)")
        print(f"   4. Download the fine-tuned model zip")

    elif args.stats:
        formatter = DatasetFormatter()
        stats     = formatter.get_stats()
        if not stats:
            print("No stats found. Run --collect first.")
        else:
            print(f"\n📊 Training Data Stats:")
            print(f"   Total pairs:    {stats.get('total_pairs', 0)}")
            print(f"   Reports used:   {stats.get('reports_used', 0)}")
            print(f"   Collected at:   {stats.get('collected_at', '?')}")
            pairs_needed = max(0, 100 - stats.get("total_pairs", 0))
            if pairs_needed > 0:
                print(f"\n   ⚠️  Need {pairs_needed} more pairs for good fine-tuning")
                print(f"      Review more PRs to collect more data")
            else:
                print(f"\n   ✅ Ready for fine-tuning!")

    else:
        parser.print_help()
        print("\nQuick start:")
        print("  1. python tools/fine_tuner.py --collect")
        print("  2. python tools/fine_tuner.py --prepare")
        print("  3. python tools/fine_tuner.py --colab-notebook")
        print("  4. Open the notebook in Google Colab and run it")


if __name__ == "__main__":
    main()