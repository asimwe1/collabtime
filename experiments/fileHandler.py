#!/usr/bin/env python3
"""
Results organization and file management for warehouse experiments.
Handles auto-incrementing run directories and output structure.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class ResultsManager:
    """Manages experiment output directories and run tracking"""
    
    def __init__(self, base_output_dir: str = 'results'):
        self.base_dir = Path(base_output_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.runs_manifest = self.base_dir / 'runs.json'
        self.current_run_dir = None
        self.current_run_id = None
    
    def get_next_run_number(self) -> int:
        """Find the next available run number"""
        existing_runs = [d for d in self.base_dir.iterdir() 
                        if d.is_dir() and d.name.startswith('run')]
        
        if not existing_runs:
            return 1
        
        run_numbers = []
        for run_dir in existing_runs:
            try:
                num = int(run_dir.name.replace('run', ''))
                run_numbers.append(num)
            except ValueError:
                continue
        
        return max(run_numbers) + 1 if run_numbers else 1
    
    def create_run_directory(self, run_id: Optional[str] = None, 
                            description: Optional[str] = None,
                            config_names: Optional[List[str]] = None,
                            config_details: Optional[Dict] = None) -> Path:
        """Create a new run directory with auto-incrementing number"""
        if run_id is None:
            run_number = self.get_next_run_number()
            run_id = f"run{run_number:03d}"
        
        self.current_run_id = run_id
        self.current_run_dir = self.base_dir / run_id
        self.current_run_dir.mkdir(parents=True, exist_ok=True)
        
        metadata = {
            'run_id': run_id,
            'timestamp': datetime.now().isoformat(),
            'description': description or '',
            'configs': config_names or [],
            'status': 'running'
        }
        
        if config_details:
            metadata['config_details'] = config_details
        
        self._save_run_metadata(metadata)
        self._update_manifest(metadata)
        
        return self.current_run_dir
    
    def get_mode_directory(self, mode: str) -> Path:
        """Get or create directory for a specific mode (centralized/distributed)"""
        if self.current_run_dir is None:
            raise RuntimeError("No run directory created yet. Call create_run_directory() first.")
        
        mode_dir = self.current_run_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        return mode_dir
    
    def mark_run_complete(self, status: str = 'completed'):
        """Mark current run as complete in metadata"""
        if self.current_run_dir is None:
            return
        
        metadata_file = self.current_run_dir / 'run_metadata.json'
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            metadata['status'] = status
            metadata['completed_at'] = datetime.now().isoformat()
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self._update_manifest(metadata)
    
    def get_latest_run(self) -> Optional[Path]:
        """Get the most recent run directory"""
        if not self.runs_manifest.exists():
            return None
        
        with open(self.runs_manifest, 'r') as f:
            manifest = json.load(f)
        
        if not manifest.get('runs'):
            return None
        
        latest = max(manifest['runs'], key=lambda r: r.get('timestamp', ''))
        return self.base_dir / latest['run_id']
    
    def get_run_by_id(self, run_id: str) -> Optional[Path]:
        """Get a specific run directory by ID"""
        run_dir = self.base_dir / run_id
        return run_dir if run_dir.exists() else None
    
    def list_runs(self, status: Optional[str] = None) -> List[Dict]:
        """List all runs, optionally filtered by status"""
        if not self.runs_manifest.exists():
            return []
        
        with open(self.runs_manifest, 'r') as f:
            manifest = json.load(f)
        
        runs = manifest.get('runs', [])
        
        if status:
            runs = [r for r in runs if r.get('status') == status]
        
        return sorted(runs, key=lambda r: r.get('timestamp', ''), reverse=True)
    
    def find_comparison_pair(self, run_id: Optional[str] = None) -> Tuple[Optional[Path], Optional[Path]]:
        """Find centralized and distributed directories for a run"""
        if run_id is None:
            run_dir = self.get_latest_run()
        else:
            run_dir = self.get_run_by_id(run_id)
        
        if run_dir is None:
            return None, None
        
        central_dir = run_dir / 'centralized'
        dist_dir = run_dir / 'distributed'
        
        central = central_dir if central_dir.exists() else None
        dist = dist_dir if dist_dir.exists() else None
        
        return central, dist
    
    def _save_run_metadata(self, metadata: Dict):
        """Save metadata for current run"""
        metadata_file = self.current_run_dir / 'run_metadata.json'
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
    
    def _update_manifest(self, run_metadata: Dict):
        """Update the global runs manifest"""
        if self.runs_manifest.exists():
            with open(self.runs_manifest, 'r') as f:
                manifest = json.load(f)
        else:
            manifest = {'runs': []}
        
        existing_idx = None
        for idx, run in enumerate(manifest['runs']):
            if run['run_id'] == run_metadata['run_id']:
                existing_idx = idx
                break
        
        if existing_idx is not None:
            manifest['runs'][existing_idx] = run_metadata
        else:
            manifest['runs'].append(run_metadata)
        
        with open(self.runs_manifest, 'w') as f:
            json.dump(manifest, f, indent=2)


def get_results_manager(base_dir: str = 'results') -> ResultsManager:
    """Factory function to get a ResultsManager instance"""
    return ResultsManager(base_dir)

