"""Inference architecture compatible with dougsm/ggcnn Cornell state dictionaries.

GG-CNN: Douglas Morrison et al., RSS 2018, https://github.com/dougsm/ggcnn.
See GGCNN-LICENSE.txt for the upstream BSD-3-Clause notice.
"""

from torch import nn
from torch.nn import functional as F


class GGCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 32, 9, stride=3, padding=3)
        self.conv2 = nn.Conv2d(32, 16, 5, stride=2, padding=2)
        self.conv3 = nn.Conv2d(16, 8, 3, stride=2, padding=1)
        self.convt1 = nn.ConvTranspose2d(8, 8, 3, stride=2, padding=1, output_padding=1)
        self.convt2 = nn.ConvTranspose2d(8, 16, 5, stride=2, padding=2, output_padding=1)
        self.convt3 = nn.ConvTranspose2d(16, 32, 9, stride=3, padding=3, output_padding=1)
        self.pos_output = nn.Conv2d(32, 1, 2)
        self.cos_output = nn.Conv2d(32, 1, 2)
        self.sin_output = nn.Conv2d(32, 1, 2)
        self.width_output = nn.Conv2d(32, 1, 2)

    def forward(self, image):
        for layer in (self.conv1, self.conv2, self.conv3, self.convt1, self.convt2, self.convt3):
            image = F.relu(layer(image))
        return tuple(layer(image) for layer in (self.pos_output, self.cos_output, self.sin_output, self.width_output))
