#!/usr/bin/env python3
"""
tools/lora_trainer.py — QLoRA Fine-Tuning pour Prométhée
Entraîne un adapter LoRA sur gemma-3-12b-it à partir d'un dataset ChatML.
Exporte en format compatible Ollama (safetensors ou GGUF).

Usage:
    python tools/lora_trainer.py --dataset datasets/strategist.jsonl --name strategist
    python tools/lora_trainer.py --dataset datasets/coder.jsonl --name coder --epochs 3
"""

import argparse
import json
import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger("LoRATrainer")

# --- Configuration par défaut ---

DEFAULT_BASE_MODEL = "google/gemma-3-12b-it"
DEFAULT_OUTPUT_DIR = "lora_adapters"
DEFAULT_MAX_SEQ_LENGTH = 2048
DEFAULT_LORA_R = 16
DEFAULT_LORA_ALPHA = 32
DEFAULT_EPOCHS = 2
DEFAULT_LR = 2e-4
DEFAULT_BATCH_SIZE = 2
DEFAULT_GRAD_ACCUM = 4
DEFAULT_WARMUP_RATIO = 0.05
DEFAULT_WEIGHT_DECAY = 0.01


def check_environment():
    """Vérifie que l'environnement est prêt pour le training."""
    import torch
    if not torch.cuda.is_available():
        print("ERREUR: CUDA non disponible. Installez PyTorch avec CUDA:")
        print("  pip install torch --index-url https://download.pytorch.org/whl/cu128")
        sys.exit(1)

    gpu_name = torch.cuda.get_device_name(0)
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU: {gpu_name} ({vram_gb:.1f} GB VRAM)")
    print(f"PyTorch: {torch.__version__}")
    print(f"CUDA: {torch.version.cuda}")
    return vram_gb


def load_dataset_chatml(dataset_path: str) -> list:
    """Charge un dataset JSONL en format ChatML."""
    examples = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            messages = data.get("messages", [])
            if len(messages) >= 2:
                examples.append({"messages": messages})
    print(f"Dataset: {len(examples)} exemples chargés depuis {dataset_path}")
    return examples


def format_for_training(examples: list, tokenizer) -> list:
    """Formate les exemples pour SFTTrainer."""
    formatted = []
    for ex in examples:
        text = tokenizer.apply_chat_template(
            ex["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        formatted.append({"text": text})
    return formatted


def try_unsloth(args, dataset_path: str) -> bool:
    """Essaie d'utiliser Unsloth (plus rapide, moins de VRAM)."""
    try:
        from unsloth import FastLanguageModel
        print("\n=== Mode Unsloth (optimisé) ===")
    except ImportError:
        print("Unsloth non disponible, fallback vers transformers+peft")
        return False

    import torch
    from trl import SFTTrainer, SFTConfig
    from datasets import Dataset

    # Charger le modèle
    print(f"Chargement du modèle {args.base_model} (4-bit)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_length,
        dtype=torch.float16,
        load_in_4bit=True,
    )

    # Configurer LoRA
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        use_rslora=True,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    # Préparer le dataset
    raw_examples = load_dataset_chatml(dataset_path)
    formatted = format_for_training(raw_examples, tokenizer)

    # Split train/eval (90/10)
    split_idx = max(1, int(len(formatted) * 0.9))
    train_data = Dataset.from_list(formatted[:split_idx])
    eval_data = Dataset.from_list(formatted[split_idx:])
    print(f"Split: {len(train_data)} train, {len(eval_data)} eval")

    # Configuration de l'entraînement
    output_dir = os.path.join(args.output_dir, args.name)
    os.makedirs(output_dir, exist_ok=True)

    training_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        logging_steps=1,
        save_strategy="epoch",
        eval_strategy="epoch",
        fp16=True,
        optim="adamw_8bit",
        seed=42,
        max_seq_length=args.max_seq_length,
        dataset_text_field="text",
        torch_compile=False,  # Triton non disponible sur Windows sans MSVC
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_data,
        eval_dataset=eval_data,
        args=training_config,
    )

    # Entraînement
    print(f"\nDébut de l'entraînement ({args.epochs} epochs)...")
    result = trainer.train()
    print(f"Training terminé. Loss finale: {result.training_loss:.4f}")

    # Sauvegarder l'adapter
    adapter_path = os.path.join(output_dir, "adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print(f"Adapter sauvegardé dans {adapter_path}")

    # Exporter en GGUF si possible
    try:
        print("Export GGUF...")
        model.save_pretrained_gguf(
            os.path.join(output_dir, "gguf"),
            tokenizer,
            quantization_method="q4_k_m",
        )
        print(f"GGUF exporté dans {output_dir}/gguf/")
    except Exception as e:
        print(f"Export GGUF non disponible: {e}")
        print("Utilisez llama.cpp pour convertir manuellement.")

    return True


def train_with_transformers(args, dataset_path: str):
    """Fallback: utilise transformers + peft + trl directement."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer, SFTConfig
    from datasets import Dataset

    print(f"\n=== Mode Transformers + PEFT ===")
    print(f"Chargement du modèle {args.base_model} (4-bit)...")

    # Quantization config
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    # Charger modèle et tokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.float16,
    )

    model = prepare_model_for_kbit_training(model)

    # LoRA config
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"Paramètres: {trainable:,} trainable / {total:,} total ({100*trainable/total:.2f}%)")

    # Préparer le dataset
    raw_examples = load_dataset_chatml(dataset_path)
    formatted = format_for_training(raw_examples, tokenizer)

    split_idx = max(1, int(len(formatted) * 0.9))
    train_data = Dataset.from_list(formatted[:split_idx])
    eval_data = Dataset.from_list(formatted[split_idx:])
    print(f"Split: {len(train_data)} train, {len(eval_data)} eval")

    # Training
    output_dir = os.path.join(args.output_dir, args.name)
    os.makedirs(output_dir, exist_ok=True)

    training_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        logging_steps=1,
        save_strategy="epoch",
        eval_strategy="epoch",
        fp16=True,
        optim="adamw_torch",
        seed=42,
        max_seq_length=args.max_seq_length,
        dataset_text_field="text",
        torch_compile=False,  # Triton non disponible sur Windows sans MSVC
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_data,
        eval_dataset=eval_data,
        args=training_config,
    )

    print(f"\nDébut de l'entraînement ({args.epochs} epochs)...")
    result = trainer.train()
    print(f"Training terminé. Loss finale: {result.training_loss:.4f}")

    # Sauvegarder
    adapter_path = os.path.join(output_dir, "adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print(f"Adapter sauvegardé dans {adapter_path}")


def create_ollama_modelfile(adapter_dir: str, name: str, base_model: str = "gemma3:12b"):
    """Crée un Modelfile Ollama pour déployer l'adapter."""
    gguf_dir = os.path.join(adapter_dir, "gguf")
    adapter_file = None

    # Chercher le fichier GGUF
    if os.path.isdir(gguf_dir):
        for f in os.listdir(gguf_dir):
            if f.endswith(".gguf"):
                adapter_file = os.path.join(gguf_dir, f)
                break

    # Chercher un adapter safetensors
    safetensors_dir = os.path.join(adapter_dir, "adapter")
    if adapter_file is None and os.path.isdir(safetensors_dir):
        for f in os.listdir(safetensors_dir):
            if f.endswith(".safetensors"):
                adapter_file = safetensors_dir
                break

    if adapter_file is None:
        print("ERREUR: Aucun adapter trouvé (GGUF ou safetensors)")
        return None

    modelfile_path = os.path.join(adapter_dir, "Modelfile")
    with open(modelfile_path, "w", encoding="utf-8") as f:
        f.write(f"FROM {base_model}\n")
        f.write(f"ADAPTER {adapter_file}\n")
        f.write(f"PARAMETER temperature 0.7\n")
        f.write(f"PARAMETER top_p 0.9\n")
        f.write(f"PARAMETER num_ctx 4096\n")

    print(f"Modelfile créé: {modelfile_path}")
    print(f"Pour déployer: ollama create promethee-{name} -f {modelfile_path}")
    return modelfile_path


def main():
    parser = argparse.ArgumentParser(description="QLoRA Fine-Tuning pour Prométhée")
    parser.add_argument("--dataset", required=True, help="Chemin vers le dataset JSONL (ChatML)")
    parser.add_argument("--name", required=True, help="Nom du LoRA (coder, strategist, etc.)")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help=f"Modèle de base (défaut: {DEFAULT_BASE_MODEL})")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Répertoire de sortie (défaut: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--max-seq-length", type=int, default=DEFAULT_MAX_SEQ_LENGTH)
    parser.add_argument("--lora-r", type=int, default=DEFAULT_LORA_R, help=f"LoRA rank (défaut: {DEFAULT_LORA_R})")
    parser.add_argument("--lora-alpha", type=int, default=DEFAULT_LORA_ALPHA, help=f"LoRA alpha (défaut: {DEFAULT_LORA_ALPHA})")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS, help=f"Nombre d'epochs (défaut: {DEFAULT_EPOCHS})")
    parser.add_argument("--lr", type=float, default=DEFAULT_LR, help=f"Learning rate (défaut: {DEFAULT_LR})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Batch size (défaut: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--grad-accum", type=int, default=DEFAULT_GRAD_ACCUM, help=f"Gradient accumulation (défaut: {DEFAULT_GRAD_ACCUM})")
    parser.add_argument("--warmup-ratio", type=float, default=DEFAULT_WARMUP_RATIO)
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--deploy", action="store_true", help="Crée le Modelfile Ollama après training")

    args = parser.parse_args()

    if not os.path.isfile(args.dataset):
        print(f"ERREUR: Dataset introuvable: {args.dataset}")
        sys.exit(1)

    print("=" * 60)
    print(f"  QLoRA Fine-Tuning — Prométhée [{args.name}]")
    print("=" * 60)
    print(f"  Modèle:    {args.base_model}")
    print(f"  Dataset:   {args.dataset}")
    print(f"  LoRA:      r={args.lora_r}, alpha={args.lora_alpha}")
    print(f"  Training:  {args.epochs} epochs, lr={args.lr}, batch={args.batch_size}x{args.grad_accum}")
    print("=" * 60)

    vram = check_environment()

    # Tenter Unsloth d'abord, fallback vers transformers+peft
    success = try_unsloth(args, args.dataset)
    if not success:
        train_with_transformers(args, args.dataset)

    # Déployer vers Ollama si demandé
    if args.deploy:
        output_dir = os.path.join(args.output_dir, args.name)
        create_ollama_modelfile(output_dir, args.name)


if __name__ == "__main__":
    main()
