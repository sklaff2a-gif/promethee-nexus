from core.agent import AgentBase

class MathWizard(AgentBase):
    def process_task(self, mission):
        """Process a math task by calculating square and cube of a number.
        
        Args:
            mission (dict): Contains a 'number' key with the integer value to process
            
        Returns:
            dict: Contains 'square' and 'cube' keys with calculated values
        """
        number = mission['number']
        
        # Calculate square and cube using closure for encapsulation
        def calculate_square(x):
            return x * x
        
        def calculate_cube(x):
            return x * x * x
        
        return {
            'square': calculate_square(number),
            'cube': calculate_cube(number)
        }