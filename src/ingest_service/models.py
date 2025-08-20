from pydantic import BaseModel, AwareDatetime, field_validator, Field, AliasChoices
from typing import Optional
import re

class Telemetry(BaseModel):
    
    timestamp: AwareDatetime = Field(validation_alias=AliasChoices('timestamp', 'event_ts'))
    equipment_id: str
    sensor_id: str
    value: Optional[float] = None
    unit: Optional[str] = None

    @field_validator('sensor_id')
    @classmethod
    def validate_sensor(cls, v: str):
        if not re.match(r'^(TBN|GNR)_[0-9]{3}_(TMP|VIB|PRS)$', v):
            raise ValueError('invalid sensor_id format')
        return v
