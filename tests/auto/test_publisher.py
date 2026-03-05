import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from core.event_bus.publisher import EventPublisher

@patch('core.event_bus.publisher.bus')
@patch('core.event_bus.publisher.logging.getLogger')
def test_event_publisher_initialization(mock_get_logger, mock_bus):
    """Test l'initialisation du logger dans EventPublisher"""
    # Configuration du mock logger
    mock_logger = MagicMock()
    mock_get_logger.return_value = mock_logger
    
    # Création de l'instance
    publisher = EventPublisher()
    
    # Vérifications
    mock_get_logger.assert_called_once_with(__name__)
    assert publisher.logger is mock_logger
    mock_logger.setLevel.assert_called_once_with(logging.INFO)
    mock_logger.addHandler.assert_called_once()
    mock_logger.addHandler.assert_called_with(mock_logger.handlers[0])

@patch('core.event_bus.publisher.bus')
@patch('core.event_bus.publisher.logging.getLogger')
@pytest.mark.asyncio
async def test_publish_success(mock_get_logger, mock_bus):
    """Test de la méthode publish avec succès"""
    # Configuration des mocks
    mock_logger = MagicMock()
    mock_get_logger.return_value = mock_logger
    mock_bus.publish = AsyncMock()
    
    # Création de l'instance
    publisher = EventPublisher()
    
    # Appel de la méthode
    await publisher.publish("test_event", {"data": "test"})
    
    # Vérifications
    mock_bus.publish.assert_called_once_with("test_event", {"data": "test"})
    mock_logger.info.assert_called_once_with("Event published successfully: test_event")

@patch('core.event_bus.publisher.bus')
@patch('core.event_bus.publisher.logging.getLogger')
@pytest.mark.asyncio
async def test_publish_failure(mock_get_logger, mock_bus):
    """Test de la méthode publish avec échec"""
    # Configuration des mocks
    mock_logger = MagicMock()
    mock_get_logger.return_value = mock_logger
    mock_bus.publish = AsyncMock(side_effect=Exception("Test error"))
    
    # Création de l'instance
    publisher = EventPublisher()
    
    # Appel de la méthode
    with pytest.raises(Exception, match="Test error"):
        await publisher.publish("error_event", {"data": "test"})
    
    # Vérifications
    mock_bus.publish.assert_called_once_with("error_event", {"data": "test"})
    mock_logger.error.assert_called_once_with("Failed to publish event error_event: Test error", exc_info=True)