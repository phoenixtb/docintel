#!/usr/bin/env python3
"""
DocIntel Evaluation Script
==========================

Runs RAGAS evaluation on the RAG pipeline using RAGBench TechQA dataset.

Usage:
    python scripts/evaluate.py --dataset techqa --samples 100
    python scripts/evaluate.py --dataset all --samples 50
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

# These will be installed when you run: pip install datasets ragas


def load_dataset(dataset_name: str, num_samples: int):
    """Load evaluation dataset from HuggingFace."""
    from datasets import load_dataset
    
    datasets_config = {
        "techqa": ("galileo-ai/ragbench", "techqa"),
        "hr_policies": ("syncora/hr-policies-qa-dataset", None),
        "hotpotqa": ("galileo-ai/ragbench", "hotpotqa"),
    }
    
    if dataset_name == "all":
        # Load samples from each dataset
        samples = []
        for name, (repo, subset) in datasets_config.items():
            if name == "hr_policies":
                continue  # Different structure
            ds = load_dataset(repo, subset, split="test")
            samples.extend(ds.select(range(min(num_samples // 3, len(ds)))))
        return samples
    
    if dataset_name not in datasets_config:
        raise ValueError(f"Unknown dataset: {dataset_name}")
    
    repo, subset = datasets_config[dataset_name]
    ds = load_dataset(repo, subset, split="test")
    return ds.select(range(min(num_samples, len(ds))))


def query_rag_service(question: str, tenant_id: str = "demo"):
    """Query the RAG service."""
    import httpx
    
    response = httpx.post(
        "http://localhost:8000/query",
        json={
            "question": question,
            "tenant_id": tenant_id,
            "user_roles": ["admin"],  # Full access for evaluation
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def run_evaluation(dataset, output_path: Path):
    """Run RAGAS evaluation on the dataset."""
    from ragas import evaluate
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    
    results = []
    
    print(f"Evaluating {len(dataset)} samples...")
    
    for i, sample in enumerate(dataset):
        question = sample.get("question") or sample.get("query")
        ground_truth = sample.get("answer") or sample.get("response")
        
        print(f"[{i+1}/{len(dataset)}] {question[:50]}...")
        
        try:
            response = query_rag_service(question)
            results.append({
                "question": question,
                "ground_truth": ground_truth,
                "answer": response["answer"],
                "contexts": [s.get("content", "") for s in response.get("sources", [])],
                "detected_domain": response.get("detected_domain"),
                "cache_hit": response.get("cache_hit"),
                "cost_usd": response.get("cost_usd"),
            })
        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                "question": question,
                "ground_truth": ground_truth,
                "answer": None,
                "error": str(e),
            })
    
    # Run RAGAS evaluation
    print("\nRunning RAGAS metrics...")
    
    # Filter successful results for RAGAS
    valid_results = [r for r in results if r.get("answer")]
    
    if valid_results:
        # Prepare data for RAGAS
        from datasets import Dataset
        
        eval_dataset = Dataset.from_dict({
            "question": [r["question"] for r in valid_results],
            "answer": [r["answer"] for r in valid_results],
            "contexts": [r["contexts"] for r in valid_results],
            "ground_truth": [r["ground_truth"] for r in valid_results],
        })
        
        scores = evaluate(
            eval_dataset,
            metrics=[
                faithfulness,
                answer_relevancy,
                context_precision,
                context_recall,
            ],
        )
        
        print("\n" + "=" * 50)
        print("RAGAS Evaluation Results")
        print("=" * 50)
        print(f"Faithfulness:      {scores['faithfulness']:.3f}")
        print(f"Answer Relevancy:  {scores['answer_relevancy']:.3f}")
        print(f"Context Precision: {scores['context_precision']:.3f}")
        print(f"Context Recall:    {scores['context_recall']:.3f}")
        print("=" * 50)
        
        # Save results
        output = {
            "timestamp": datetime.now().isoformat(),
            "num_samples": len(dataset),
            "num_successful": len(valid_results),
            "scores": dict(scores),
            "results": results,
        }
    else:
        output = {
            "timestamp": datetime.now().isoformat(),
            "num_samples": len(dataset),
            "num_successful": 0,
            "error": "No successful queries",
            "results": results,
        }
    
    output_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved to: {output_path}")
    
    return output


def main():
    parser = argparse.ArgumentParser(description="DocIntel RAGAS Evaluation")
    parser.add_argument(
        "--dataset",
        choices=["techqa", "hr_policies", "hotpotqa", "all"],
        default="techqa",
        help="Dataset to evaluate on",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=100,
        help="Number of samples to evaluate",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation_results.json"),
        help="Output file path",
    )
    
    args = parser.parse_args()
    
    print("=" * 50)
    print("DocIntel RAGAS Evaluation")
    print("=" * 50)
    print(f"Dataset: {args.dataset}")
    print(f"Samples: {args.samples}")
    print(f"Output:  {args.output}")
    print()
    
    dataset = load_dataset(args.dataset, args.samples)
    run_evaluation(dataset, args.output)


if __name__ == "__main__":
    main()
