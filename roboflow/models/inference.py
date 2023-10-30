import io
import urllib

import requests
from PIL import Image
import time
import json
from typing import List
# import magic
from urllib.parse import urljoin
from requests_toolbelt.multipart.encoder import MultipartEncoder

from roboflow.util.image_utils import validate_image_path
from roboflow.util.prediction import PredictionGroup

from roboflow.config import API_URL

API_URL = "https://api.roboflow.one"

SUPPORTED_ROBOFLOW_MODELS = ["batch-video"]

SUPPORTED_ADDITIONAL_MODELS = {
    "clip": {
        "model_id": "clip",
        "model_version": "1",
        "inference_type": "clip-embed-image"
    },
    "gaze": {
        "model_id": "gaze",
        "model_version": "1",
        "inference_type": "gaze-detection"
    }
}


def is_valid_mime(filename):
    mime = magic.Magic(mime=True)
    file_type = mime.from_file(filename)
    return file_type in ["video/mp4", "video/avi", "video/webm"]


def is_valid_video(filename):
    # # check file type
    # if not is_valid_mime(filename):
    #     return False
    
    return True

class InferenceModel:
    def __init__(
        self,
        api_key,
        version_id,
        colors=None,
        *args,
        **kwargs,
    ):
        """
        Create an InferenceModel object through which you can run inference.

        Args:
            api_key (str): private roboflow api key
            version_id (str): the ID of the dataset version to use for inference
        """

        self.__api_key = api_key
        self.id = version_id

        version_info = self.id.rsplit("/")
        self.dataset_id = version_info[1]
        self.version = version_info[2]
        self.colors = {} if colors is None else colors

    def __get_image_params(self, image_path):
        """
        Get parameters about an image (i.e. dimensions) for use in an inference request.

        Args:
            image_path (str): path to the image you'd like to perform prediction on

        Returns:
            Tuple containing a dict of querystring params and a dict of requests kwargs

        Raises:
            Exception: Image path is not valid
        """
        validate_image_path(image_path)

        hosted_image = urllib.parse.urlparse(image_path).scheme in ("http", "https")

        if hosted_image:
            image_dims = {"width": "Undefined", "height": "Undefined"}
            return {"image": image_path}, {}, image_dims

        image = Image.open(image_path)
        dimensions = image.size
        image_dims = {"width": str(dimensions[0]), "height": str(dimensions[1])}
        buffered = io.BytesIO()
        image.save(buffered, quality=90, format="JPEG")
        data = MultipartEncoder(
            fields={"file": ("imageToUpload", buffered.getvalue(), "image/jpeg")}
        )
        return (
            {},
            {"data": data, "headers": {"Content-Type": data.content_type}},
            image_dims,
        )

    def predict(self, image_path, prediction_type=None, **kwargs):
        """
        Infers detections based on image from a specified model and image path.

        Args:
            image_path (str): path to the image you'd like to perform prediction on
            prediction_type (str): type of prediction to perform
            **kwargs: Any additional kwargs will be turned into querystring params

        Returns:
            PredictionGroup Object

        Raises:
            Exception: Image path is not valid

        Example:
            >>> import roboflow

            >>> rf = roboflow.Roboflow(api_key="")

            >>> project = rf.workspace().project("PROJECT_ID")

            >>> model = project.version("1").model

            >>> prediction = model.predict("YOUR_IMAGE.jpg")
        """
        params, request_kwargs, image_dims = self.__get_image_params(image_path)

        params["api_key"] = self.__api_key

        params.update(**kwargs)

        url = f"{self.api_url}?{urllib.parse.urlencode(params)}"
        response = requests.post(url, **request_kwargs)
        response.raise_for_status()

        return PredictionGroup.create_prediction_group(
            response.json(),
            image_path=image_path,
            prediction_type=prediction_type,
            image_dims=image_dims,
            colors=self.colors,
        )


    def predict_video(
        self,
        video_path: str,
        fps: int = 5,
        additional_models: list = [],
        prediction_type: str = "batch-video",
    ) -> List[str]:
        """
        Infers detections based on image from specified model and image path.

        Args:
            video_path (str): path to the video you'd like to perform prediction on
            prediction_type (str): type of the model to run
            fps (int): frames per second to run inference

        Returns:
            A list of the signed url and job id

        Example:
            >>> import roboflow

            >>> rf = roboflow.Roboflow(api_key="")

            >>> project = rf.workspace().project("PROJECT_ID")

            >>> model = project.version("1").model

            >>> prediction = model.predict("video.mp4", fps=5, inference_type="object-detection")
        """

        url = urljoin(API_URL, "/video_upload_signed_url?api_key=" + self.__api_key)

        if fps > 5:
            raise Exception("FPS must be less than or equal to 5.")
        
        for model in additional_models:
            if model not in SUPPORTED_ADDITIONAL_MODELS:
                raise Exception(f"Model {model} is not supported for video inference.")
            
        if prediction_type not in SUPPORTED_ROBOFLOW_MODELS:
            raise Exception(f"{prediction_type} is not supported for video inference.")

        if not is_valid_video(video_path):
            raise Exception("Video path is not valid")

        payload = json.dumps(
            {
                "file_name": video_path,
            }
        )

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.request("POST", url, headers=headers, data=payload)
        except Exception as e:
            raise Exception(f"Error uploading video: {e}")
        
        signed_url = response.json()["signed_url"]

        print("Uploaded video to signed url: " + signed_url)

        url = urljoin(API_URL, "/videoinfer/?api_key=" + self.__api_key)

        # check if ObjectDetectionModel, ClassificationModel, or InstanceSegmentationModel
        model_class = self.__class__.__name__

        if model_class == "ObjectDetectionModel":
            self.type = "object-detection"
        elif model_class == "ClassificationModel":
            self.type = "classification"
        elif model_class == "InstanceSegmentationModel":
            self.type = "instance-segmentation"
        else:
            raise Exception("Model type not supported for video inference.")

        models = [
            {
                "model_id": self.dataset_id,
                "model_version": self.version,
                "inference_type": self.type,
            }
        ]

        for model in additional_models:
            models.append(SUPPORTED_ADDITIONAL_MODELS[model])

        payload = json.dumps(
            {
                "input_url": signed_url,
                "infer_fps": 5,
                "models": models
            }
        )

        response = requests.request("POST", url, headers=headers, data=payload)

        job_id = response.json()["job_id"]

        self.job_id = job_id

        return job_id, signed_url

    def poll_for_video_results(self, job_id: str = None) -> dict:
        """
        Polls the Roboflow API to check if video inference is complete.

        Returns:
            Inference results as a dict

        Example:
            >>> import roboflow

            >>> rf = roboflow.Roboflow(api_key="")

            >>> project = rf.workspace().project("PROJECT_ID")

            >>> model = project.version("1").model

            >>> prediction = model.predict("video.mp4")

            >>> results = model.poll_for_video_results()
        """

        if job_id is None:
            job_id = self.job_id

        url = urljoin(
            API_URL, "/videoinfer/?api_key=" + self.__api_key + "&job_id=" + self.job_id
        )

        response = requests.get(url, headers={"Content-Type": "application/json"})

        data = response.json()

        # for model in infer_models:
        #     if model["infer_success"] == 1:
        if data.get("output_signed_url") is None:
            return {}
        
        output_signed_url = data["output_signed_url"]

        inference_data = requests.get(
            output_signed_url, headers={"Content-Type": "application/json"}
        )

        # frame_offset and model name are top-level keys
        return inference_data.json()

    def poll_until_video_results(self, job_id) -> dict:
        """
        Polls the Roboflow API to check if video inference is complete.

        When inference is complete, the results are returned.

        Returns:
            Inference results as a dict

        Example:
            >>> import roboflow

            >>> rf = roboflow.Roboflow(api_key="")

            >>> project = rf.workspace().project("PROJECT_ID")

            >>> model = project.version("1").model

            >>> prediction = model.predict("video.mp4")

            >>> results = model.poll_until_results()
        """
        if job_id is None:
            job_id = self.job_id

        attempts = 0

        while True:
            print(f"({attempts * 60}s): Checking for inference results")

            response = self.poll_for_video_results()

            time.sleep(60)

            attempts += 1

            if response != {}:
                return response