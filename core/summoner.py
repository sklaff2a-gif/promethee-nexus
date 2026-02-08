import importlib.util
import os

class Summoner:
    def __init__(self, module_name):
        self.module_name = module_name
        self.path = os.path.join(os.path.dirname(__file__), 'grimoire', f"{module_name}.py")
        
        # Valider le nom du module
        if not self.module_name.replace('_', '').isalnum():
            raise ValueError(f"Invalid module name: {self.module_name}")

    def load(self):
        """Charge dynamiquement le module spécifié"""
        if not os.path.exists(self.path):
            raise ImportError(f"Module not found: {self.path}")

        # Créer une spécification du module
        spec = importlib.util.spec_from_file_location(
            self.module_name, self.path
        )
        
        # Créer le module
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        return module

    def __repr__(self):
        return f"Summoner(module_name={self.module_name}, path={self.path})"

# Exemple d'utilisation
if __name__ == "__main__":
    with open(os.path.join(os.path.dirname(__file__), 'grimoire', 'demo.py'), 'w') as f:
        f.write('def example():\n    return "Hello from dynamic import!"')
    
    summoner = Summoner('demo')
    demo_module = summoner.load()
    print(demo_module.example())