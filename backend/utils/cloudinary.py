"""
Cloudinary Integration for Spinr
Provides image upload, transformation, and management capabilities.
"""
import cloudinary
import cloudinary.uploader
import cloudinary.api
from typing import Optional, Dict, Any, List
from loguru import logger


class CloudinaryService:
    """Service for Cloudinary image management."""
    
    def __init__(
        self,
        cloud_name: str = '',
        api_key: str = '',
        api_secret: str = '',
        secure: bool = True
    ):
        """
        Initialize Cloudinary service.
        
        Args:
            cloud_name: Cloudinary cloud name
            api_key: Cloudinary API key
            api_secret: Cloudinary API secret
            secure: Use HTTPS for URLs
        """
        self.configured = bool(cloud_name and api_key and api_secret)
        
        if self.configured:
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=secure
            )
            logger.info('Cloudinary configured successfully')
        else:
            logger.warning('Cloudinary not configured - using mock mode')
    
    async def upload_image(
        self,
        file_path: str,
        folder: str = 'spinr',
        public_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        transformation: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Upload an image to Cloudinary.
        
        Args:
            file_path: Path to the file to upload
            folder: Folder in Cloudinary to store the image
            public_id: Optional custom public ID for the image
            tags: Optional list of tags for the image
            transformation: Optional transformation options
        
        Returns:
            Dict with upload result including secure_url, public_id, etc.
        """
        if not self.configured:
            logger.info(f'[MOCK] Would upload image: {file_path}')
            return {
                'success': True,
                'secure_url': f'https://mock.cloudinary.com/{folder}/{file_path}',
                'public_id': public_id or file_path,
                'format': file_path.split('.')[-1] if '.' in file_path else 'jpg',
                'width': 800,
                'height': 600
            }
        
        try:
            upload_options = {
                'folder': folder,
                'tags': tags or [],
                'resource_type': 'image'
            }
            
            if public_id:
                upload_options['public_id'] = public_id
            
            if transformation:
                upload_options.update(transformation)
            
            result = cloudinary.uploader.upload(file_path, **upload_options)
            
            logger.info(f'Image uploaded to Cloudinary: {result["public_id"]}')
            
            return {
                'success': True,
                'secure_url': result['secure_url'],
                'public_id': result['public_id'],
                'format': result['format'],
                'width': result.get('width', 0),
                'height': result.get('height', 0),
                'bytes': result.get('bytes', 0)
            }
        except Exception as e:
            logger.error(f'Failed to upload image to Cloudinary: {e}')
            return {
                'success': False,
                'error': str(e)
            }
    
    async def upload_from_url(
        self,
        image_url: str,
        folder: str = 'spinr',
        public_id: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Upload an image from a URL to Cloudinary.
        
        Args:
            image_url: URL of the image to upload
            folder: Folder in Cloudinary to store the image
            public_id: Optional custom public ID for the image
            tags: Optional list of tags for the image
        
        Returns:
            Dict with upload result
        """
        if not self.configured:
            logger.info(f'[MOCK] Would upload image from URL: {image_url}')
            return {
                'success': True,
                'secure_url': image_url,
                'public_id': public_id or 'mock_image'
            }
        
        try:
            result = cloudinary.uploader.upload(
                image_url,
                folder=folder,
                public_id=public_id,
                tags=tags or [],
                resource_type='image'
            )
            
            logger.info(f'Image uploaded from URL to Cloudinary: {result["public_id"]}')
            
            return {
                'success': True,
                'secure_url': result['secure_url'],
                'public_id': result['public_id']
            }
        except Exception as e:
            logger.error(f'Failed to upload image from URL: {e}')
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_image_url(
        self,
        public_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        crop: str = 'fill',
        quality: str = 'auto',
        format: str = 'auto'
    ) -> str:
        """
        Generate a transformed image URL.
        
        Args:
            public_id: Public ID of the image in Cloudinary
            width: Optional target width
            height: Optional target height
            crop: Crop mode (fill, fit, crop, thumb, etc.)
            quality: Quality setting (auto, best, good, eco, low)
            format: Output format (auto, jpg, png, webp)
        
        Returns:
            Transformed image URL
        """
        if not self.configured:
            return f'https://mock.cloudinary.com/{public_id}'
        
        transformations = []
        
        if width and height:
            transformations.append(f'w_{width}')
            transformations.append(f'h_{height}')
            transformations.append(f'c_{crop}')
        elif width:
            transformations.append(f'w_{width}')
        elif height:
            transformations.append(f'h_{height}')
        
        transformations.append(f'q_{quality}')
        transformations.append(f'f_{format}')
        
        transformation_str = ','.join(transformations)
        
        return cloudinary.utils.cloudinary_url(
            public_id,
            transformation=transformation_str
        )[0]
    
    async def delete_image(self, public_id: str) -> Dict[str, Any]:
        """
        Delete an image from Cloudinary.
        
        Args:
            public_id: Public ID of the image to delete
        
        Returns:
            Dict with deletion result
        """
        if not self.configured:
            logger.info(f'[MOCK] Would delete image: {public_id}')
            return {'success': True, 'result': 'ok'}
        
        try:
            result = cloudinary.uploader.destroy(public_id)
            
            logger.info(f'Image deleted from Cloudinary: {public_id}')
            
            return {
                'success': True,
                'result': result.get('result', 'ok')
            }
        except Exception as e:
            logger.error(f'Failed to delete image from Cloudinary: {e}')
            return {
                'success': False,
                'error': str(e)
            }
    
    async def list_images(
        self,
        folder: str = 'spinr',
        max_results: int = 100
    ) -> Dict[str, Any]:
        """
        List images in a Cloudinary folder.
        
        Args:
            folder: Folder to list images from
            max_results: Maximum number of results to return
    
        Returns:
            Dict with list of images
        """
        if not self.configured:
            logger.info(f'[MOCK] Would list images in folder: {folder}')
            return {
                'success': True,
                'resources': [],
                'total': 0
            }
        
        try:
            result = cloudinary.api.resources(
                type='upload',
                prefix=folder + '/',
                max_results=max_results
            )
            
            return {
                'success': True,
                'resources': result.get('resources', []),
                'total': len(result.get('resources', []))
            }
        except Exception as e:
            logger.error(f'Failed to list images from Cloudinary: {e}')
            return {
                'success': False,
                'error': str(e)
            }


# Global service instance
_cloudinary_service: Optional[CloudinaryService] = None


def get_cloudinary_service() -> CloudinaryService:
    """Get or create the global Cloudinary service instance."""
    global _cloudinary_service
    if _cloudinary_service is None:
        _cloudinary_service = CloudinaryService()
    return _cloudinary_service


def init_cloudinary(
    cloud_name: str,
    api_key: str,
    api_secret: str
) -> CloudinaryService:
    """
    Initialize the global Cloudinary service.
    
    Args:
        cloud_name: Cloudinary cloud name
        api_key: Cloudinary API key
        api_secret: Cloudinary API secret
    
    Returns:
        Configured CloudinaryService instance
    """
    global _cloudinary_service
    _cloudinary_service = CloudinaryService(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret
    )
    return _cloudinary_service